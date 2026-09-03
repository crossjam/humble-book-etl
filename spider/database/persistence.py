from datetime import datetime, timezone
from typing import Iterable

import logging
from sqlalchemy import create_engine, inspect, or_, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..config.settings import Settings
from ..schemas.bundle import BundleRecord
from ..schemas.raw_data import LandingPageRawDataRecord
from .models import Base, Bundle, LandingPageRawData
from .session import build_database_uri

logger = logging.getLogger(__name__)


def ensure_landing_page_raw_data_table(engine) -> None:
    """
    Verifica y crea la tabla landing_page_raw_data si no existe.
    
    Args:
        engine: Motor de SQLAlchemy para ejecutar las consultas.
    """
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    
    if 'landing_page_raw_data' not in table_names:
        logger.info('Creando tabla landing_page_raw_data...')
        try:
            with engine.begin() as connection:
                connection.execute(text("""
                    CREATE TABLE landing_page_raw_data (
                        id VARCHAR NOT NULL,
                        json_data TEXT NOT NULL,
                        scraped_date TIMESTAMP NOT NULL,
                        source_url VARCHAR NOT NULL,
                        json_hash VARCHAR,
                        json_version VARCHAR,
                        PRIMARY KEY (id)
                    )
                """))
                connection.execute(text('CREATE INDEX ix_landing_page_raw_data_scraped_date ON landing_page_raw_data (scraped_date)'))
                connection.execute(text('CREATE INDEX ix_landing_page_raw_data_json_hash ON landing_page_raw_data (json_hash)'))
                logger.info('Tabla landing_page_raw_data creada exitosamente')
        except Exception as exc:
            logger.warning('Error creando tabla landing_page_raw_data (puede que ya exista): %s', exc)
    else:
        logger.debug('La tabla landing_page_raw_data ya existe')


def ensure_columns(engine) -> None:
    inspector = inspect(engine)
    
    # Verificar que la tabla bundle existe antes de intentar obtener columnas
    if 'bundle' not in inspector.get_table_names():
        logger.warning('La tabla bundle no existe, se creará automáticamente')
        return
    
    try:
        columns = {column['name'] for column in inspector.get_columns('bundle')}
    except Exception as exc:
        logger.warning('No se pudieron obtener columnas de la tabla bundle: %s', exc)
        return
    
    statements = []
    if 'duration_days' not in columns:
        statements.append('ALTER TABLE bundle ADD COLUMN duration_days REAL')
    if 'is_active' not in columns:
        statements.append('ALTER TABLE bundle ADD COLUMN is_active BOOLEAN DEFAULT 0')
    if 'price_tiers' not in columns:
        statements.append('ALTER TABLE bundle ADD COLUMN price_tiers TEXT')
    if 'book_list' not in columns:
        statements.append('ALTER TABLE bundle ADD COLUMN book_list TEXT')
    if 'featured_image' not in columns:
        statements.append('ALTER TABLE bundle ADD COLUMN featured_image VARCHAR')
    if 'tile_logo' not in columns:
        statements.append('ALTER TABLE bundle ADD COLUMN tile_logo VARCHAR')
    if 'msrp_total' not in columns:
        statements.append('ALTER TABLE bundle ADD COLUMN msrp_total REAL')
    if 'raw_html' not in columns:
        statements.append('ALTER TABLE bundle ADD COLUMN raw_html TEXT')
    archive_column_needed = 'archived_at' not in columns
    
    for stmt in statements:
        try:
            with engine.begin() as connection:
                connection.execute(text(stmt))
                logger.info('Columna agregada: %s', stmt)
        except Exception as exc:
            logger.warning('Error agregando columna %s: %s', stmt, exc)

    if archive_column_needed:
        try:
            with engine.begin() as connection:
                connection.execute(text('ALTER TABLE bundle ADD COLUMN archived_at TIMESTAMP'))
                logger.info('Columna agregada: ALTER TABLE bundle ADD COLUMN archived_at TIMESTAMP')
        except Exception as exc:
            raise RuntimeError('Could not add required bundle.archived_at column') from exc

    if 'archived_at' in columns or archive_column_needed:
        try:
            with engine.begin() as connection:
                connection.execute(text(
                    'CREATE INDEX IF NOT EXISTS ix_bundle_archived_at ON bundle (archived_at)'
                ))
                if 'verification_date' in {
                    column['name'] for column in inspect(engine).get_columns('bundle')
                }:
                    connection.execute(text(
                        'CREATE INDEX IF NOT EXISTS ix_bundle_verification_date ON bundle (verification_date)'
                    ))
                result = connection.execute(_archive_state_update())
                if result.rowcount:
                    logger.info('Normalized %s archived bundles as inactive', result.rowcount)
        except Exception as exc:
            raise RuntimeError('Could not initialize bundle archive state') from exc


def _archive_state_update():
    """Build a cross-dialect update enforcing archived-implies-inactive."""
    return update(Bundle).where(
        Bundle.archived_at.is_not(None),
        Bundle.is_active.is_not(False),
    ).values(is_active=False)


def _utc_naive(value: datetime) -> datetime:
    """Normalize aware timestamps to the naive UTC used by SQLite columns."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _is_expired(end_date: datetime | None, now: datetime) -> bool:
    return end_date is not None and _utc_naive(end_date) < _utc_naive(now)


def _is_current_payload(payload: dict, now: datetime) -> bool:
    start_date = payload.get('start_date_datetime')
    end_date = payload.get('end_date_datetime')
    return (
        payload.get('is_active') is True
        and start_date is not None
        and end_date is not None
        and _utc_naive(start_date) <= _utc_naive(now)
        and not _is_expired(end_date, now)
    )


def persist_bundles(
    records: Iterable[BundleRecord],
    session: Session,
    now: datetime | None = None,
) -> None:
    """
    Persiste los bundles en la base de datos SQLite.
    
    Inserta o actualiza los bundles usando machine_name como clave única.
    Para SQLite, usa INSERT OR REPLACE.
    
    Args:
        records: Iterable de BundleRecord a persistir.
        session: Sesión de SQLAlchemy para la transacción.
        
    Raises:
        RuntimeError: Si ocurre un error al guardar los bundles en la BD.
    """
    current_time = now or datetime.utcnow()
    for record in records:
        payload = record.to_orm_payload()
        try:
            # Buscar si ya existe un bundle con el mismo machine_name
            existing = session.query(Bundle).filter(Bundle.machine_name == payload['machine_name']).first()
            if existing:
                # Actualizar el bundle existente
                for key, value in payload.items():
                    if key != 'id':  # No actualizar el ID
                        setattr(existing, key, value)
                if _is_current_payload(payload, current_time):
                    # A reappearing bundle keeps its identity and becomes current again.
                    existing.is_active = True
                    existing.archived_at = None
                elif _is_expired(payload.get('end_date_datetime'), current_time):
                    existing.is_active = False
                    if existing.archived_at is None:
                        existing.archived_at = current_time
                elif payload.get('is_active') is True:
                    # Never restore an active state without a valid, future end date.
                    existing.is_active = False
            else:
                if _is_current_payload(payload, current_time):
                    payload['archived_at'] = None
                elif _is_expired(payload.get('end_date_datetime'), current_time):
                    payload['is_active'] = False
                    payload['archived_at'] = current_time
                elif payload.get('is_active') is True:
                    payload['is_active'] = False
                # Insertar un bundle nuevo
                bundle = Bundle(**payload)
                session.add(bundle)
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise RuntimeError(f'Error guardando bundles: {exc}') from exc


def persist_landing_page_raw_data(record: LandingPageRawDataRecord, session: Session) -> None:
    """
    Persiste el raw data de landingPage-json-data en la base de datos.
    
    Inserta un nuevo registro con el JSON raw obtenido del script
    landingPage-json-data junto con su metadata.
    
    Args:
        record: LandingPageRawDataRecord con el JSON y metadata a persistir.
        session: Sesión de SQLAlchemy para la transacción.
        
    Raises:
        RuntimeError: Si ocurre un error al guardar el raw data en la BD.
    """
    payload = record.to_orm_payload()
    try:
        landing_page_raw_data = LandingPageRawData(**payload)
        session.add(landing_page_raw_data)
        session.commit()
        logger.info('Raw data de landingPage guardado exitosamente')
    except SQLAlchemyError as exc:
        session.rollback()
        raise RuntimeError(f'Error guardando raw data de landingPage: {exc}') from exc


def remove_outdated_bundles(
    session: Session,
    now: datetime | None = None,
) -> None:
    """Archive expired bundles without deleting their normalized metadata."""
    current_time = now or datetime.utcnow()
    expired = session.query(Bundle).filter(
        Bundle.end_date_datetime < current_time,
        or_(Bundle.is_active.is_(True), Bundle.archived_at.is_(None)),
    ).all()

    archived_count = 0
    for bundle in expired:
        bundle.is_active = False
        if bundle.archived_at is None:
            bundle.archived_at = current_time
            archived_count += 1

    session.commit()
    logger.info('Archived %s expired bundles', archived_count)


def recreate_database(settings: Settings, drop_existing: bool = True) -> None:
    """
    Elimina y recrea la base de datos SQLite.
    
    Args:
        settings: Configuración de la base de datos
        drop_existing: Si True, elimina la base de datos existente antes de crearla
    """
    from pathlib import Path
    
    db_path = Path(settings.db_path)
    
    if drop_existing and db_path.exists():
        logger.info('Eliminando base de datos existente: %s', settings.db_path)
        db_path.unlink()
    
    uri = build_database_uri(settings)
    engine = create_engine(uri, echo=settings.sql_echo, future=True, connect_args={'check_same_thread': False})
    
    logger.info('Creando tablas...')
    # Usar checkfirst=True para evitar conflictos
    Base.metadata.create_all(engine, checkfirst=True)
    ensure_columns(engine)
    ensure_landing_page_raw_data_table(engine)
    logger.info('Base de datos recreada exitosamente')
