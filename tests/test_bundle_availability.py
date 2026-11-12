"""Public discovery must not trust stale ETL availability flags."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

import api.main as api
import hbetl_mcp.catalog as mcp_catalog
from hbetl_mcp.availability import active_bundle_filter, bundle_is_active
from hbetl_mcp.catalog import BundleCatalog
from spider.config.settings import Settings
from spider.database.models import Base, Bundle


NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
NOW_DB = NOW.replace(tzinfo=None)
CURRENT_IDS = {"current", "starts-now"}
ENDED_IDS = {"expired-stale-flag", "ends-now"}


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW_DB

    monkeypatch.setattr(api, "datetime", FrozenDateTime)
    monkeypatch.setattr(mcp_catalog, "datetime", FrozenDateTime)
    db_path = tmp_path / "availability.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    schedules = [
        ("current", {}),
        ("starts-now", {"start_date_datetime": NOW_DB, "msrp_total": 20}),
        ("expired-stale-flag", {"end_date_datetime": NOW_DB - timedelta(microseconds=1)}),
        ("ends-now", {"end_date_datetime": NOW_DB}),
        ("future-start", {"start_date_datetime": NOW_DB + timedelta(microseconds=1)}),
        ("unknown-start", {"start_date_datetime": None}),
        ("unknown-end", {"end_date_datetime": None}),
        ("inactive", {"is_active": False}),
        ("archived-stale-flag", {"archived_at": NOW_DB - timedelta(days=1)}),
        ("legacy-inactive", {"is_active": False, "end_date_datetime": None}),
    ]
    with Session(engine) as session:
        for identifier, overrides in schedules:
            values = {
                "id": identifier,
                "machine_name": f"{identifier}-bundle",
                "is_active": True,
                "start_date_datetime": NOW_DB - timedelta(days=1),
                "end_date_datetime": NOW_DB + timedelta(days=1),
                "verification_date": NOW_DB - timedelta(hours=1),
                "msrp_total": 10 if identifier == "current" else 1000,
                "book_list": [{
                    "machine_name": f"{identifier}-book",
                    "title": f"Availability book {identifier}",
                }],
            }
            session.add(Bundle(**(values | overrides)))
        session.commit()
    engine.dispose()

    catalog = BundleCatalog(Settings(db_type="sqlite", db_path=str(db_path)))
    yield catalog
    catalog.engine.dispose()


@pytest.fixture
def api_requests(catalog):
    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{catalog.engine.url.database}"
    )
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)

    async def override_db():
        async with sessions() as session:
            yield session

    api.app.dependency_overrides[api.get_async_db] = override_db

    def request(path):
        async def exercise():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api.app),
                base_url="http://test",
            ) as client:
                return await client.get(path)
        return asyncio.run(exercise())

    yield request
    api.app.dependency_overrides.pop(api.get_async_db, None)
    asyncio.run(async_engine.dispose())


def test_public_api_excludes_stale_future_and_unknown_schedules(api_requests):
    response = api_requests("/bundles")
    assert response.status_code == 200
    assert {bundle["id"] for bundle in response.json()} == CURRENT_IDS

    featured = api_requests("/bundles/featured")
    assert featured.status_code == 200
    assert featured.json()["id"] == "starts-now"

    retained = api_requests("/bundles?include_inactive=true")
    assert retained.status_code == 200
    assert ENDED_IDS <= {bundle["id"] for bundle in retained.json()}
    # Public reads do not mutate archive state or the retained source flags.
    expired = api_requests("/bundles/by-machine-name/expired-stale-flag-bundle")
    assert expired.status_code == 200
    assert expired.json()["is_active"] is True


def test_history_includes_expired_true_flags_and_expiry_equality(api_requests):
    response = api_requests("/bundle-history")
    assert response.status_code == 200
    assert {item["id"] for item in response.json()} == ENDED_IDS | {"legacy-inactive"}


def test_api_featured_returns_404_when_only_stale_candidates_remain(catalog, api_requests):
    with catalog.session() as session:
        for bundle in session.scalars(select(Bundle).where(Bundle.id.in_(CURRENT_IDS))):
            bundle.is_active = False
        session.commit()

    assert api_requests("/bundles").json() == []
    assert api_requests("/bundles/featured").status_code == 404


def test_mcp_discovery_and_activity_metadata_use_current_dates(catalog):
    assert {bundle.id for bundle in catalog.list_bundles()} == CURRENT_IDS
    assert catalog.featured_bundle().id == "starts-now"
    assert {book.bundle_id for book in catalog.search_books("Availability book")} == CURRENT_IDS

    retained = {bundle.id: bundle for bundle in catalog.list_bundles(include_inactive=True)}
    assert len(retained) == 10
    assert {identifier for identifier, bundle in retained.items() if bundle.is_active} == CURRENT_IDS
    assert catalog.get_bundle("expired-stale-flag").is_active is False

    for identifier in ENDED_IDS | {"future-start", "unknown-start", "unknown-end"}:
        for lookup in (identifier, f"{identifier}-bundle"):
            assert catalog.search_books("Availability book", bundle_identifier=lookup) == []
            archived_books = catalog.search_books(
                "Availability book", bundle_identifier=lookup, include_inactive=True,
            )
            assert len(archived_books) == 1
            assert archived_books[0].bundle_is_active is False
        with pytest.raises(ValueError, match="Book not found"):
            catalog.get_book(f"{identifier}-book", bundle_identifier=identifier)


def test_mcp_featured_rejects_all_stale_candidates(catalog):
    with catalog.session() as session:
        for bundle in session.scalars(select(Bundle).where(Bundle.id.in_(CURRENT_IDS))):
            bundle.is_active = False
        session.commit()

    with pytest.raises(ValueError, match="No active bundles"):
        catalog.featured_bundle()


@pytest.mark.parametrize("offset", [timedelta(hours=5, minutes=30), timedelta(hours=-8)])
def test_offset_aware_clock_uses_naive_utc_database_parameters(catalog, offset):
    local_now = NOW.astimezone(timezone(offset))
    statement = select(Bundle).where(active_bundle_filter(local_now))
    # PostgreSQL TIMESTAMP WITHOUT TIME ZONE, like SQLite DateTime, must receive
    # normalized UTC wall-clock values, not timezone-dependent aware parameters.
    parameters = statement.compile(dialect=postgresql.dialect()).params
    timestamps = [value for value in parameters.values() if isinstance(value, datetime)]
    assert len(timestamps) == 2
    assert all(value == NOW_DB and value.tzinfo is None for value in timestamps)

    with catalog.session() as session:
        assert {bundle.id for bundle in session.scalars(statement)} == CURRENT_IDS
        for bundle in session.scalars(select(Bundle)):
            assert bundle_is_active(bundle, local_now) is (bundle.id in CURRENT_IDS)


def test_in_memory_availability_normalizes_offset_aware_bundle_dates():
    offset = timezone(timedelta(hours=5, minutes=30))
    bundle = Bundle(
        is_active=True,
        start_date_datetime=(NOW - timedelta(hours=1)).astimezone(offset),
        end_date_datetime=(NOW + timedelta(hours=1)).astimezone(offset),
    )
    assert bundle_is_active(bundle, NOW) is True
    bundle.end_date_datetime = NOW.astimezone(offset)
    assert bundle_is_active(bundle, NOW) is False
