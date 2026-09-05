from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Iterable

import logging
from sqlalchemy import create_engine, inspect, or_, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..config.settings import Settings
from ..schemas.bundle import BundleRecord
from ..schemas.raw_data import LandingPageRawDataRecord
from .models import Base, Bundle, BundleLifecycleEvent, LandingPageRawData
from .session import build_database_uri

logger = logging.getLogger(__name__)


def ensure_bundle_lifecycle_event_table(engine) -> None:
    """Create the append-only lifecycle event table on existing deployments."""
    try:
        BundleLifecycleEvent.__table__.create(engine, checkfirst=True)
    except Exception as exc:
        raise RuntimeError('Could not initialize bundle lifecycle event table') from exc


def _event_key(
    machine_name: str,
    event_type: str,
    previous_start_at: datetime | None,
    previous_end_at: datetime | None,
    new_start_at: datetime | None,
    new_end_at: datetime | None,
    occurrence: str,
) -> str:
    values = (
        machine_name,
        event_type,
        previous_start_at.isoformat() if previous_start_at else '',
        previous_end_at.isoformat() if previous_end_at else '',
        new_start_at.isoformat() if new_start_at else '',
        new_end_at.isoformat() if new_end_at else '',
        occurrence,
    )
    return sha256('|'.join(values).encode('utf-8')).hexdigest()


def _classify_schedule_change(
    previous_start_at: datetime | None,
    previous_end_at: datetime | None,
    new_start_at: datetime | None,
    new_end_at: datetime | None,
    *,
    was_inactive: bool = False,
    is_current: bool = False,
) -> str | None:
    """Classify a source-observed schedule transition."""
    previous_start_at = _utc_naive(previous_start_at) if previous_start_at else None
    previous_end_at = _utc_naive(previous_end_at) if previous_end_at else None
    new_start_at = _utc_naive(new_start_at) if new_start_at else None
    new_end_at = _utc_naive(new_end_at) if new_end_at else None

    if (
        previous_start_at is not None
        and new_start_at is not None
        and new_start_at > previous_start_at
    ):
        return 'renewed'
    if previous_end_at is not None and new_end_at is not None:
        if new_end_at > previous_end_at:
            return 'extended'
        if new_end_at < previous_end_at:
            return 'shortened'
    if was_inactive and is_current:
        return 'reactivated'
    return None


def _add_lifecycle_event(
    session: Session,
    *,
    machine_name: str,
    event_type: str,
    observed_at: datetime,
    previous_start_at: datetime | None,
    previous_end_at: datetime | None,
    new_start_at: datetime | None,
    new_end_at: datetime | None,
    bundle_id: str | None = None,
    bundle_title: str | None = None,
    source_snapshot_id: str | None = None,
) -> bool:
    previous_start_at = _utc_naive(previous_start_at) if previous_start_at else None
    previous_end_at = _utc_naive(previous_end_at) if previous_end_at else None
    new_start_at = _utc_naive(new_start_at) if new_start_at else None
    new_end_at = _utc_naive(new_end_at) if new_end_at else None
    observed_at = _utc_naive(observed_at)
    occurrence = source_snapshot_id or observed_at.isoformat()
    key = _event_key(
        machine_name,
        event_type,
        previous_start_at,
        previous_end_at,
        new_start_at,
        new_end_at,
        occurrence,
    )
    if session.query(BundleLifecycleEvent.id).filter_by(event_key=key).first():
        return False

    event = BundleLifecycleEvent(
        event_key=key,
        bundle_id=bundle_id,
        machine_name=machine_name,
        bundle_title=bundle_title,
        event_type=event_type,
        observed_at=observed_at,
        previous_start_at=previous_start_at,
        previous_end_at=previous_end_at,
        new_start_at=new_start_at,
        new_end_at=new_end_at,
        source_snapshot_id=source_snapshot_id,
    )
    try:
        with session.begin_nested():
            session.add(event)
            session.flush()
    except IntegrityError:
        return False
    return True


def _track_existing_bundle_change(
    session: Session,
    existing: Bundle,
    payload: dict,
    current_time: datetime,
    source_snapshot_id: str | None = None,
) -> None:
    previous_start_at = existing.start_date_datetime
    previous_end_at = existing.end_date_datetime
    new_start_at = payload.get('start_date_datetime')
    new_end_at = payload.get('end_date_datetime')
    event_type = _classify_schedule_change(
        previous_start_at,
        previous_end_at,
        new_start_at,
        new_end_at,
        was_inactive=existing.is_active is False or existing.archived_at is not None,
        is_current=_is_current_payload(payload, current_time),
    )
    if event_type:
        _add_lifecycle_event(
            session,
            machine_name=existing.machine_name,
            bundle_id=existing.id,
            bundle_title=payload.get('tile_name') or existing.tile_name,
            event_type=event_type,
            observed_at=current_time,
            previous_start_at=previous_start_at,
            previous_end_at=previous_end_at,
            new_start_at=new_start_at,
            new_end_at=new_end_at,
            source_snapshot_id=source_snapshot_id,
        )


def _extract_snapshot_products(payload: dict) -> dict[str, dict]:
    products: dict[str, dict] = {}
    for section in payload.get('data', {}).values():
        if not isinstance(section, dict):
            continue
        for mosaic in section.get('mosaic', []):
            if not isinstance(mosaic, dict):
                continue
            for product in mosaic.get('products', []):
                if isinstance(product, dict) and product.get('machine_name'):
                    products[product['machine_name']] = product
    return products


def _snapshot_datetime(product: dict, key: str) -> datetime | None:
    value = product.get(key)
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return _utc_naive(parsed)


def backfill_lifecycle_events_from_raw_data(session: Session) -> int:
    """Backfill deterministic schedule-change events from stored raw snapshots."""
    snapshots = session.query(LandingPageRawData).order_by(
        LandingPageRawData.scraped_date,
        LandingPageRawData.id,
    ).all()
    previous_products: dict[str, dict] = {}
    bundle_rows = {
        bundle.machine_name: bundle
        for bundle in session.query(Bundle).all()
    }
    created = 0

    for snapshot in snapshots:
        payload = snapshot.json_data
        if isinstance(payload, str):
            payload = json.loads(payload)
        current_products = _extract_snapshot_products(payload)
        for machine_name, product in current_products.items():
            previous = previous_products.get(machine_name)
            if previous is None:
                continue
            previous_start_at = _snapshot_datetime(previous, 'start_date|datetime')
            previous_end_at = _snapshot_datetime(previous, 'end_date|datetime')
            new_start_at = _snapshot_datetime(product, 'start_date|datetime')
            new_end_at = _snapshot_datetime(product, 'end_date|datetime')
            event_type = _classify_schedule_change(
                previous_start_at,
                previous_end_at,
                new_start_at,
                new_end_at,
            )
            if event_type is None:
                continue
            bundle = bundle_rows.get(machine_name)
            if _add_lifecycle_event(
                session,
                machine_name=machine_name,
                bundle_id=bundle.id if bundle else None,
                bundle_title=product.get('tile_name') or product.get('tile_short_name'),
                event_type=event_type,
                observed_at=snapshot.scraped_date,
                previous_start_at=previous_start_at,
                previous_end_at=previous_end_at,
                new_start_at=new_start_at,
                new_end_at=new_end_at,
                source_snapshot_id=snapshot.id,
            ):
                created += 1
        previous_products.update(current_products)

    session.commit()
    logger.info('Backfilled %s bundle lifecycle events', created)
    return created


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
    source_snapshot_id: str | None = None,
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
                _track_existing_bundle_change(
                    session,
                    existing,
                    payload,
                    current_time,
                    source_snapshot_id=source_snapshot_id,
                )
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


def persist_landing_page_raw_data(
    record: LandingPageRawDataRecord,
    session: Session,
) -> LandingPageRawData:
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
        return landing_page_raw_data
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
    ensure_bundle_lifecycle_event_table(engine)
    logger.info('Base de datos recreada exitosamente')
