from pathlib import Path
import os
from datetime import datetime, timedelta, timezone
from api.security import create_access_token, decode_access_token, verify_password
from api.schemas import (
    BundleHistoryResponse,
    BundleLifecycleEventResponse,
    BundleResponse,
    BundleRawHtmlResponse,
    ETLRunResponse,
    LandingPageRawDataResponse,
    LoginRequest,
    TokenResponse,
    UserResponse,
)
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import and_, nulls_last, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

import logging

from jose import JWTError

from spider.database.session import get_session_factory as build_session_factory
from spider.database.persistence import (
    persist_bundles,
    remove_outdated_bundles,
    persist_landing_page_raw_data,
)
from spider.core.spider import HumbleSpider
from spider.core.errors import HumbleSpiderError
from spider.database.models import Bundle, BundleLifecycleEvent, LandingPageRawData, User
from spider.config.settings import get_settings
from spider.database.seed import ensure_admin_user

logger = logging.getLogger(__name__)


settings = get_settings()
SessionFactory = None
AsyncSessionFactory = None
oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/auth/login')


def get_async_engine():
    """Creates the async engine for FastAPI using SQLite or PostgreSQL."""
    from pathlib import Path
    from spider.database.session import build_database_uri

    uri = build_database_uri(settings)

    # Para async, necesitamos usar asyncpg para PostgreSQL
    if settings.db_type == 'postgresql':
        # Reemplazar postgresql:// con postgresql+asyncpg://
        if uri.startswith('postgresql://'):
            uri = uri.replace('postgresql://', 'postgresql+asyncpg://', 1)
    else:  # sqlite
        # Reemplazar sqlite:// con sqlite+aiosqlite://
        if uri.startswith('sqlite:///'):
            uri = uri.replace('sqlite:///', 'sqlite+aiosqlite:///', 1)

    return create_async_engine(uri, echo=settings.sql_echo, future=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan to initialize async resources."""
    global AsyncSessionFactory
    async_engine = get_async_engine()

    # Log database connection info
    logger.info(f"Connecting to database: {settings.db_type.upper()}")
    if settings.db_type == 'postgresql':
        logger.info(f"PostgreSQL connection: {settings.pghost}:{
                    settings.pgport}/{settings.pgdatabase}")
    else:
        logger.info(f"SQLite database path: {settings.db_path}")

    # Create tables if they don't exist using the async engine
    from spider.database.models import Base
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)

    # Ensure columns exist (works better with sync)
    from spider.database.persistence import (
        ensure_bundle_lifecycle_event_table,
        ensure_columns,
        ensure_landing_page_raw_data_table,
    )
    from spider.database.session import build_database_uri
    from sqlalchemy import create_engine

    sync_uri = build_database_uri(settings)
    connect_args = {}
    if settings.db_type == 'sqlite':
        connect_args = {'check_same_thread': False}

    sync_engine = create_engine(
        sync_uri,
        echo=settings.sql_echo,
        future=True,
        connect_args=connect_args
    )
    try:
        ensure_columns(sync_engine)
        ensure_landing_page_raw_data_table(sync_engine)
        ensure_bundle_lifecycle_event_table(sync_engine)
        SessionLocal = sessionmaker(
            bind=sync_engine, expire_on_commit=False, class_=Session)
        try:
            with SessionLocal() as sync_session:
                result = ensure_admin_user(sync_session, settings)
                if result == 'created':
                    logger.info(
                        "Usuario admin '%s' creado durante el inicio de la API.", settings.admin_username)
                elif result == 'updated':
                    logger.info(
                        "Usuario admin '%s' actualizado durante el inicio de la API.", settings.admin_username)
        except Exception as exc:
            logger.error("Error seeding admin user: %s", exc)
    finally:
        sync_engine.dispose()

    AsyncSessionFactory = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
    yield
    await async_engine.dispose()

app = FastAPI(
    title='Humble Bundle ETL API',
    version='1.0.2',
    description='API v1.0.2 - Scraper original de Humble Bundle. Trigger ETL and query stored bundles.',
    lifespan=lifespan,
    redoc_url=None,
    docs_url=None,
)


@app.get('/docs', include_in_schema=False, response_class=HTMLResponse)
async def swagger_ui_html() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url='/openapi.json',
        title=f'{app.title} - Swagger UI',
        oauth2_redirect_url='/docs/oauth2-redirect',
        swagger_js_url='https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js',
        swagger_css_url='https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css',
    )


@app.get('/docs/oauth2-redirect', include_in_schema=False, response_class=HTMLResponse)
async def swagger_ui_redirect() -> HTMLResponse:
    return get_swagger_ui_oauth2_redirect_html()


@app.get('/redoc', include_in_schema=False, response_class=HTMLResponse)
async def redoc() -> HTMLResponse:
    return get_redoc_html(
        openapi_url='/openapi.json',
        title=f'{app.title} - ReDoc',
        redoc_js_url='https://cdn.jsdelivr.net/npm/redoc@latest/bundles/redoc.standalone.js',
    )

allowed_origins = [
    'http://localhost:3002',
    'http://127.0.0.1:3002',
    'http://frontend:3002',
    'http://localhost:3003',
    'http://127.0.0.1:3003',
    'https://projects.dopeldev.com',
    'https://humble-book-etl-crossjam.exe.xyz:3002',
    'https://hbetl.aegean-skate.ts.net',
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
    expose_headers=['X-Snapshot-At'],
)

# Montar directorio de imágenes estáticas (local development)

# Usar ruta relativa al directorio del proyecto
images_dir = Path(__file__).parent.parent / "images"
# Crear directorios si no existen
images_dir.mkdir(parents=True, exist_ok=True)
(images_dir / "bundles").mkdir(parents=True, exist_ok=True)
(images_dir / "books").mkdir(parents=True, exist_ok=True)

app.mount("/images", StaticFiles(directory=str(images_dir)), name="images")


def get_db():
    global SessionFactory
    if SessionFactory is None:
        SessionFactory = build_session_factory(settings)
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()


async def get_async_db():
    """Async session for async endpoints."""
    global AsyncSessionFactory
    if AsyncSessionFactory is None:
        async_engine = get_async_engine()
        AsyncSessionFactory = async_sessionmaker(
            async_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
    async with AsyncSessionFactory() as session:
        try:
            yield session
        finally:
            await session.close()


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Credenciales inválidas',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    try:
        payload = decode_access_token(token, settings)
        user_id: str | None = payload.get('sub')
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.get(User, user_id)
    if user is None:
        raise credentials_exception
    return user


@app.post('/auth/login', response_model=TokenResponse, tags=['auth'])
def login(credentials: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == credentials.username).first()
    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Usuario o contraseña inválidos',
        )
    access_token = create_access_token(
        {'sub': user.id, 'username': user.username}, settings)
    return TokenResponse(access_token=access_token, user=user)


@app.get('/auth/me', response_model=UserResponse, tags=['auth'])
def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user


@app.get('/health', tags=['health'])
async def healthcheck():
    return {'status': 'ok', 'database': settings.db_path}


@app.get('/bundles/featured', response_model=BundleResponse, tags=['bundles'])
async def get_featured_bundle(db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(
        select(Bundle).where(
            Bundle.is_active.is_(True),
            Bundle.archived_at.is_(None),
        ).order_by(
            nulls_last(Bundle.msrp_total.desc()),
            nulls_last(Bundle.bundles_sold_decimal.desc()),
        ).limit(1)
    )
    bundle = result.scalar_one_or_none()
    if not bundle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='No bundles stored')
    return bundle


@app.get('/bundles', response_model=list[BundleResponse], tags=['bundles'])
async def list_bundles(
    include_inactive: Annotated[bool, Query(description='Include archived/inactive bundles')] = False,
    limit: Annotated[int | None, Query(ge=1, le=1000, description='Maximum bundles to return')] = None,
    offset: Annotated[int, Query(ge=0, description='Number of bundles to skip')] = 0,
    snapshot_at: Annotated[datetime | None, Query(description='Fixed UTC snapshot boundary')] = None,
    before_end_date: Annotated[datetime | None, Query(description='UTC end date cursor')] = None,
    before_id: Annotated[str | None, Query(description='Bundle ID cursor')] = None,
    db: AsyncSession = Depends(get_async_db),
    response: Response = None,
):
    statement = select(Bundle)
    if not include_inactive:
        statement = statement.where(
            Bundle.is_active.is_(True),
            Bundle.archived_at.is_(None),
        )
    provided_snapshot = snapshot_at is not None
    if include_inactive and offset > 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='include_inactive pagination requires cursors instead of offset',
        )
    if before_id is not None and not provided_snapshot:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='pagination cursors require snapshot_at',
        )
    if include_inactive and snapshot_at is None:
        snapshot_at = datetime.now(timezone.utc)

    if offset > 0 and (snapshot_at is not None or before_id is not None or before_end_date is not None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='offset cannot be combined with snapshot or cursor pagination',
        )

    if snapshot_at is not None:
        if snapshot_at.tzinfo is None or snapshot_at.utcoffset() != timedelta(0):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail='snapshot_at must be an offset-aware UTC timestamp',
            )
        if snapshot_at > datetime.now(timezone.utc) + timedelta(seconds=5):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail='snapshot_at cannot be in the future',
            )
        snapshot_db = snapshot_at.astimezone(timezone.utc).replace(tzinfo=None)
        statement = statement.where(Bundle.verification_date <= snapshot_db)

    if before_end_date is not None and before_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='before_end_date requires before_id',
        )
    if before_end_date is not None:
        if before_end_date.tzinfo is None or before_end_date.utcoffset() != timedelta(0):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail='before_end_date must be an offset-aware UTC timestamp',
            )
        cursor_end_date = before_end_date.astimezone(timezone.utc).replace(tzinfo=None)
        statement = statement.where(or_(
            Bundle.end_date_datetime < cursor_end_date,
            and_(Bundle.end_date_datetime == cursor_end_date, Bundle.id < before_id),
            Bundle.end_date_datetime.is_(None),
        ))
    elif before_id is not None:
        statement = statement.where(
            Bundle.end_date_datetime.is_(None),
            Bundle.id < before_id,
        )

    if include_inactive and response is not None:
        response.headers['X-Snapshot-At'] = snapshot_at.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

    page_size = limit if limit is not None else (100 if include_inactive else None)
    ordered_statement = statement.order_by(
        nulls_last(Bundle.end_date_datetime.desc()),
        Bundle.id.desc(),
    )
    if page_size is not None:
        ordered_statement = ordered_statement.offset(offset).limit(page_size)
    elif offset:
        ordered_statement = ordered_statement.offset(offset)
    result = await db.execute(ordered_statement)
    bundles = result.scalars().all()
    return bundles


@app.get('/bundles/by-machine-name/{machine_name}', response_model=BundleResponse, tags=['bundles'])
async def get_bundle_by_machine_name(machine_name: str, db: AsyncSession = Depends(get_async_db)):
    """Gets a bundle by machine_name, including retained inactive bundles."""
    result = await db.execute(
        select(Bundle).filter(Bundle.machine_name == machine_name)
    )
    bundle = result.scalar_one_or_none()
    if not bundle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Bundle not found')
    return bundle


@app.get(
    '/bundle-lifecycle-events',
    response_model=list[BundleLifecycleEventResponse],
    tags=['bundle-lifecycle'],
)
async def list_bundle_lifecycle_events(
    machine_name: Annotated[str | None, Query(description='Filter by bundle machine name')] = None,
    event_type: Annotated[
        Literal['extended', 'renewed', 'shortened', 'reactivated'] | None,
        Query(description='Filter by lifecycle event type'),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: AsyncSession = Depends(get_async_db),
):
    """List observed bundle renewals, extensions, shortenings, and reactivations."""
    statement = select(BundleLifecycleEvent)
    if machine_name is not None:
        statement = statement.where(BundleLifecycleEvent.machine_name == machine_name)
    if event_type is not None:
        statement = statement.where(BundleLifecycleEvent.event_type == event_type)
    result = await db.execute(
        statement.order_by(
            BundleLifecycleEvent.observed_at.desc(),
            BundleLifecycleEvent.id.desc(),
        ).offset(offset).limit(limit)
    )
    return result.scalars().all()


@app.get(
    '/bundle-history',
    response_model=list[BundleHistoryResponse],
    tags=['bundle-lifecycle'],
)
async def list_bundle_history(
    event_type: Annotated[
        Literal['extended', 'renewed', 'shortened', 'reactivated'] | None,
        Query(description='Filter lifecycle history by event type'),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: AsyncSession = Depends(get_async_db),
):
    """Return closed inactive bundle rows and lifecycle events in one history view."""
    now_db = datetime.now(timezone.utc).replace(tzinfo=None)
    inactive_result = await db.execute(
        select(Bundle).where(
            and_(
                or_(
                    Bundle.is_active.is_(False),
                    Bundle.archived_at.is_not(None),
                ),
                or_(
                    Bundle.end_date_datetime.is_(None),
                    Bundle.end_date_datetime <= now_db,
                ),
            )
        )
    )
    inactive_bundles = inactive_result.scalars().all()

    event_statement = select(BundleLifecycleEvent).where(or_(
        BundleLifecycleEvent.new_end_at.is_(None),
        BundleLifecycleEvent.new_end_at <= now_db,
    ))
    if event_type is not None:
        event_statement = event_statement.where(
            BundleLifecycleEvent.event_type == event_type
        )
    event_result = await db.execute(
        event_statement.order_by(
            BundleLifecycleEvent.observed_at.desc(),
            BundleLifecycleEvent.id.desc(),
        )
    )
    events = event_result.scalars().all()

    all_bundle_result = await db.execute(select(Bundle))
    all_bundles_by_machine = {
        bundle.machine_name: bundle for bundle in all_bundle_result.scalars().all()
    }
    history: list[tuple[datetime, dict]] = []
    event_bundle_machines: set[str] = set()

    for event in events:
        bundle = all_bundles_by_machine.get(event.machine_name)
        event_bundle_machines.add(event.machine_name)
        history.append((
            event.observed_at,
            {
                'id': event.id,
                'record_type': 'lifecycle_event',
                'machine_name': event.machine_name,
                'bundle_title': event.bundle_title or (bundle.tile_name if bundle else None),
                'bundle': bundle,
                'event': event,
            },
        ))

    for bundle in inactive_bundles:
        if bundle.machine_name in event_bundle_machines:
            continue
        history.append((
            bundle.verification_date,
            {
                'id': bundle.id,
                'record_type': 'inactive_bundle',
                'machine_name': bundle.machine_name,
                'bundle_title': bundle.tile_name,
                'bundle': bundle,
                'event': None,
            },
        ))

    history.sort(key=lambda item: (item[0], item[1]['id']), reverse=True)
    return [item for _, item in history[offset:offset + limit]]
@app.get(
    '/bundles/{bundle_id}/lifecycle-events',
    response_model=list[BundleLifecycleEventResponse],
    tags=['bundle-lifecycle'],
)
async def get_bundle_lifecycle_events(
    bundle_id: str,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: AsyncSession = Depends(get_async_db),
):
    """List lifecycle events for a retained bundle row."""
    result = await db.execute(
        select(BundleLifecycleEvent).where(
            BundleLifecycleEvent.bundle_id == bundle_id
        ).order_by(
            BundleLifecycleEvent.observed_at.desc(),
            BundleLifecycleEvent.id.desc(),
        ).offset(offset).limit(limit)
    )
    return result.scalars().all()


@app.get('/bundles/{bundle_id}/raw-html', response_model=BundleRawHtmlResponse, tags=['bundles'])
async def get_bundle_raw_html(
    bundle_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    """Gets retained raw bundle HTML for any authenticated user."""
    result = await db.execute(
        select(Bundle).filter(Bundle.id == bundle_id)
    )
    bundle = result.scalar_one_or_none()
    if not bundle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Bundle not found')
    logger.info('Raw HTML access bundle=%s user=%s', bundle_id, current_user.id)
    return bundle


@app.get('/bundles/{bundle_id}', response_model=BundleResponse, tags=['bundles'])
async def get_bundle(bundle_id: str, db: AsyncSession = Depends(get_async_db)):
    """Gets a bundle by UUID, including retained inactive bundles."""
    result = await db.execute(
        select(Bundle).filter(Bundle.id == bundle_id)
    )
    bundle = result.scalar_one_or_none()
    if not bundle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Bundle not found')
    return bundle


@app.post('/etl/run', response_model=ETLRunResponse, tags=['etl'])
def trigger_etl(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Executes the ETL: downloads bundles and saves to the database."""
    try:
        with HumbleSpider() as spider:
            records = spider.fetch_bundles()
            raw_data_record = spider.get_raw_data_record()
    except HumbleSpiderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    raw_data = None
    if raw_data_record:
        raw_data = persist_landing_page_raw_data(raw_data_record, db)

    observed_at = raw_data_record.scraped_date if raw_data_record else None
    remove_outdated_bundles(db, now=observed_at)
    persist_bundles(
        records,
        db,
        now=observed_at,
        source_snapshot_id=raw_data.id if raw_data else None,
    )

    return ETLRunResponse(
        bundles_processed=len(records),
        cleanup_ran=True
    )


@app.get('/landing-page-raw-data', response_model=list[LandingPageRawDataResponse], tags=['raw-data'])
async def list_landing_page_raw_data(db: AsyncSession = Depends(get_async_db)):
    """Lists all raw data records ordered by descending date."""
    result = await db.execute(
        select(LandingPageRawData).order_by(
            LandingPageRawData.scraped_date.desc())
    )
    raw_data_list = result.scalars().all()
    return raw_data_list


@app.get('/landing-page-raw-data/latest', response_model=LandingPageRawDataResponse, tags=['raw-data'])
async def get_latest_landing_page_raw_data(db: AsyncSession = Depends(get_async_db)):
    """Gets the most recent raw data record."""
    result = await db.execute(
        select(LandingPageRawData).order_by(
            LandingPageRawData.scraped_date.desc()).limit(1)
    )
    raw_data = result.scalar_one_or_none()
    if not raw_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='No raw data stored')
    return raw_data


@app.get('/landing-page-raw-data/{raw_data_id}', response_model=LandingPageRawDataResponse, tags=['raw-data'])
async def get_landing_page_raw_data(raw_data_id: str, db: AsyncSession = Depends(get_async_db)):
    """Gets a specific raw data record by its ID."""
    result = await db.execute(
        select(LandingPageRawData).filter(LandingPageRawData.id == raw_data_id)
    )
    raw_data = result.scalar_one_or_none()
    if not raw_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Raw data not found')
    return raw_data
