import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from api.main import app, get_async_db, get_current_user, get_featured_bundle, list_bundles
from api.schemas import BundleResponse
from spider.database.models import Base, Bundle
from spider.database.persistence import (
    _archive_state_update,
    ensure_columns,
    persist_bundles,
    remove_outdated_bundles,
)
from spider.schemas.bundle import BundleRecord


@pytest.fixture
def engine():
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "bundles.db"
        test_engine = create_engine(f"sqlite:///{db_path}", future=True)
        Base.metadata.create_all(test_engine)
        yield test_engine
        test_engine.dispose()


def test_existing_schema_gets_archive_timestamp_column(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE bundle")
        connection.exec_driver_sql(
            "CREATE TABLE bundle (id VARCHAR PRIMARY KEY, machine_name VARCHAR UNIQUE NOT NULL)"
        )

    ensure_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("bundle")}
    assert "archived_at" in columns


def test_expired_bundle_is_archived_instead_of_deleted(engine):
    now = datetime(2026, 9, 3, 12, 0)
    with Session(engine) as session:
        session.add(Bundle(
            id="bundle-1",
            machine_name="bundle-one",
            start_date_datetime=now - timedelta(days=10),
            end_date_datetime=now - timedelta(hours=1),
            verification_date=now - timedelta(hours=2),
            is_active=True,
        ))
        session.commit()

        remove_outdated_bundles(session, now=now)

        retained = session.get(Bundle, "bundle-1")
        assert retained is not None
        assert retained.is_active is False
        assert retained.archived_at == now


def test_archiving_is_idempotent_and_preserves_timestamp_for_current_period(engine):
    now = datetime(2026, 9, 3, 12, 0)
    first_archived_at = now - timedelta(minutes=30)
    with Session(engine) as session:
        session.add(Bundle(
            id="bundle-1",
            machine_name="bundle-one",
            end_date_datetime=now - timedelta(hours=1),
            verification_date=now - timedelta(hours=2),
            is_active=False,
            archived_at=first_archived_at,
        ))
        session.commit()

        remove_outdated_bundles(session, now=now)

        retained = session.get(Bundle, "bundle-1")
        assert retained.archived_at == first_archived_at


def test_public_bundle_response_omits_raw_html():
    assert "raw_html" not in BundleResponse.model_fields


def test_archive_state_normalization_is_postgresql_compatible():
    sql = str(_archive_state_update().compile(dialect=postgresql.dialect()))
    assert "IS NOT false" in sql
    assert "!= 0" not in sql


def test_api_pagination_can_use_a_fixed_snapshot(engine):
    snapshot = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot_db = snapshot.replace(tzinfo=None)
    with Session(engine) as session:
        session.add_all([
            Bundle(
                id="before-snapshot",
                machine_name="before-snapshot",
                is_active=False,
                archived_at=snapshot_db - timedelta(days=1),
                end_date_datetime=snapshot_db - timedelta(days=2),
                verification_date=snapshot_db - timedelta(minutes=1),
            ),
            Bundle(
                id="after-snapshot",
                machine_name="after-snapshot",
                is_active=False,
                archived_at=snapshot_db,
                end_date_datetime=snapshot_db - timedelta(days=1),
                verification_date=snapshot_db + timedelta(minutes=1),
            ),
        ])
        session.commit()

    async def exercise():
        async_engine = create_async_engine(f"sqlite+aiosqlite:///{engine.url.database}")
        async with async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)() as session:
            page = await list_bundles(
                include_inactive=True,
                limit=100,
                offset=0,
                snapshot_at=snapshot,
                db=session,
            )
        await async_engine.dispose()
        return page

    assert [bundle.id for bundle in asyncio.run(exercise())] == ["before-snapshot"]


def test_api_list_supports_bounded_offset_pagination(engine):
    now = datetime(2026, 9, 3, 12, 0)
    with Session(engine) as session:
        session.add_all([
            Bundle(
                id=f"active-{index}",
                machine_name=f"active-{index}",
                is_active=True,
                end_date_datetime=now + timedelta(days=index + 1),
            )
            for index in range(3)
        ])
        session.commit()

    async def exercise():
        async_engine = create_async_engine(f"sqlite+aiosqlite:///{engine.url.database}")
        async with async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)() as session:
            page = await list_bundles(include_inactive=False, limit=1, offset=1, db=session)
        await async_engine.dispose()
        return page

    page = asyncio.run(exercise())
    assert len(page) == 1
    assert page[0].id == "active-1"


def test_api_list_defaults_to_active_bundles(engine):
    now = datetime(2026, 9, 3, 12, 0)
    with Session(engine) as session:
        session.add_all([
            Bundle(id="active", machine_name="active", is_active=True, end_date_datetime=now + timedelta(days=1)),
            Bundle(
                id="archived",
                machine_name="archived",
                is_active=False,
                archived_at=now - timedelta(days=1),
                end_date_datetime=now - timedelta(days=2),
            ),
        ])
        session.commit()

    async def exercise():
        async_engine = create_async_engine(f"sqlite+aiosqlite:///{engine.url.database}")
        async with async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)() as session:
            default = await list_bundles(include_inactive=False, db=session)
            all_bundles = await list_bundles(include_inactive=True, db=session)
        await async_engine.dispose()
        return default, all_bundles

    default, all_bundles = asyncio.run(exercise())
    assert [bundle.id for bundle in default] == ["active"]
    assert {bundle.id for bundle in all_bundles} == {"active", "archived"}


def test_api_featured_excludes_archived_bundles(engine):
    now = datetime(2026, 9, 3, 12, 0)
    with Session(engine) as session:
        session.add_all([
            Bundle(
                id="active",
                machine_name="active",
                is_active=True,
                end_date_datetime=now + timedelta(days=1),
                msrp_total=10,
            ),
            Bundle(
                id="archived",
                machine_name="archived",
                is_active=False,
                archived_at=now - timedelta(days=1),
                end_date_datetime=now - timedelta(days=2),
                msrp_total=1000,
            ),
        ])
        session.commit()

    async def exercise():
        async_engine = create_async_engine(f"sqlite+aiosqlite:///{engine.url.database}")
        async with async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)() as session:
            featured = await get_featured_bundle(db=session)
        await async_engine.dispose()
        return featured

    assert asyncio.run(exercise()).id == "active"


def test_machine_name_route_returns_archived_bundle_over_http(engine):
    with Session(engine) as session:
        session.add(Bundle(
            id="archived",
            machine_name="archived-name",
            is_active=False,
            archived_at=datetime(2026, 9, 2, 12, 0),
            end_date_datetime=datetime(2026, 9, 1, 12, 0),
        ))
        session.commit()

    async def override_async_db():
        async_engine = create_async_engine(f"sqlite+aiosqlite:///{engine.url.database}")
        try:
            async with async_sessionmaker(
                async_engine, class_=AsyncSession, expire_on_commit=False
            )() as session:
                yield session
        finally:
            await async_engine.dispose()

    app.dependency_overrides[get_async_db] = override_async_db

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/bundles/by-machine-name/archived-name")

    try:
        response = asyncio.run(exercise())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["id"] == "archived"


def test_raw_html_requires_authenticated_replacement_endpoint(engine):
    with Session(engine) as session:
        session.add(Bundle(
            id="bundle-with-html",
            machine_name="bundle-with-html",
            is_active=True,
            end_date_datetime=datetime(2026, 9, 10, 12, 0),
            raw_html="<html>retained</html>",
        ))
        session.commit()

    async def override_async_db():
        async_engine = create_async_engine(f"sqlite+aiosqlite:///{engine.url.database}")
        try:
            async with async_sessionmaker(
                async_engine, class_=AsyncSession, expire_on_commit=False
            )() as session:
                yield session
        finally:
            await async_engine.dispose()

    app.dependency_overrides[get_async_db] = override_async_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="operator")

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/bundles/bundle-with-html/raw-html")

    try:
        response = asyncio.run(exercise())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["raw_html"] == "<html>retained</html>"


def test_reappearing_bundle_reactivates_same_row(engine):
    now = datetime(2026, 9, 3, 12, 0)
    with Session(engine) as session:
        session.add(Bundle(
            id="bundle-1",
            machine_name="bundle-one",
            end_date_datetime=now - timedelta(hours=1),
            verification_date=now - timedelta(hours=2),
            is_active=False,
            archived_at=now - timedelta(days=1),
        ))
        session.commit()

        record = BundleRecord(
            machine_name="bundle-one",
            start_date_datetime=now - timedelta(days=1),
            end_date_datetime=now + timedelta(days=6),
            verification_date=now,
            is_active=True,
        )
        persist_bundles([record], session, now=now)

        reappeared = session.query(Bundle).filter_by(machine_name="bundle-one").one()
        assert reappeared.id == "bundle-1"
        assert reappeared.is_active is True
        assert reappeared.archived_at is None
        assert reappeared.end_date_datetime == now + timedelta(days=6)


def test_expired_incoming_record_cannot_reactivate_archived_bundle(engine):
    now = datetime(2026, 9, 3, 12, 0)
    archived_at = now - timedelta(days=1)
    with Session(engine) as session:
        session.add(Bundle(
            id="bundle-1",
            machine_name="bundle-one",
            end_date_datetime=now - timedelta(hours=1),
            verification_date=archived_at,
            is_active=False,
            archived_at=archived_at,
        ))
        session.commit()

        stale_record = BundleRecord(
            machine_name="bundle-one",
            start_date_datetime=now - timedelta(days=2),
            end_date_datetime=now - timedelta(hours=1),
            verification_date=now,
            is_active=True,
        )
        persist_bundles([stale_record], session, now=now)

        retained = session.query(Bundle).filter_by(machine_name="bundle-one").one()
        assert retained.is_active is False
        assert retained.archived_at == archived_at


def test_existing_archived_active_state_is_normalized(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE bundle")
        connection.exec_driver_sql(
            "CREATE TABLE bundle ("
            "id VARCHAR PRIMARY KEY, machine_name VARCHAR UNIQUE NOT NULL, "
            "is_active BOOLEAN, archived_at TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "INSERT INTO bundle (id, machine_name, is_active, archived_at) "
            "VALUES ('bundle-1', 'bundle-one', 1, '2026-09-02 12:00:00')"
        )

    ensure_columns(engine)

    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT is_active FROM bundle WHERE id = 'bundle-1'"
        ).one()
    assert row[0] == 0


def test_api_archive_pagination_uses_keyset_cursor(engine):
    snapshot = datetime.now(timezone.utc).replace(microsecond=0)
    now = snapshot.replace(tzinfo=None)
    with Session(engine) as session:
        session.add_all([
            Bundle(
                id=f"archived-{index}",
                machine_name=f"archived-{index}",
                is_active=False,
                archived_at=now - timedelta(days=1),
                end_date_datetime=now - timedelta(days=index + 1),
                verification_date=now - timedelta(minutes=1),
            )
            for index in range(3)
        ])
        session.commit()

    async def exercise():
        async_engine = create_async_engine(f"sqlite+aiosqlite:///{engine.url.database}")
        async with async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)() as session:
            first = await list_bundles(
                include_inactive=True,
                limit=2,
                snapshot_at=snapshot,
                db=session,
            )
            second = await list_bundles(
                include_inactive=True,
                limit=2,
                snapshot_at=snapshot,
                before_end_date=first[-1].end_date_datetime.replace(tzinfo=timezone.utc),
                before_id=first[-1].id,
                db=session,
            )
        await async_engine.dispose()
        return first, second

    first, second = asyncio.run(exercise())
    assert [bundle.id for bundle in first] == ["archived-0", "archived-1"]
    assert [bundle.id for bundle in second] == ["archived-2"]


def test_snapshot_at_requires_timezone_aware_utc(engine):
    with pytest.raises(HTTPException, match="UTC"):
        asyncio.run(list_bundles(include_inactive=True, snapshot_at=datetime(2026, 9, 3, 12, 0), db=None))
def test_archive_pagination_rejects_mixed_offset_and_cursor(engine):
    snapshot = datetime.now(timezone.utc).replace(microsecond=0)
    with pytest.raises(HTTPException, match="offset"):
        asyncio.run(list_bundles(
            include_inactive=True,
            offset=1,
            snapshot_at=snapshot,
            db=None,
        ))


def test_raw_html_endpoint_denies_unauthenticated_requests(engine):
    with Session(engine) as session:
        session.add(Bundle(
            id="private-html",
            machine_name="private-html",
            is_active=True,
            end_date_datetime=datetime(2026, 9, 10, 12, 0),
            raw_html="<html>private</html>",
        ))
        session.commit()

    async def override_async_db():
        async_engine = create_async_engine(f"sqlite+aiosqlite:///{engine.url.database}")
        try:
            async with async_sessionmaker(
                async_engine, class_=AsyncSession, expire_on_commit=False
            )() as session:
                yield session
        finally:
            await async_engine.dispose()

    app.dependency_overrides[get_async_db] = override_async_db

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/bundles/private-html/raw-html")

    try:
        response = asyncio.run(exercise())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert "private" not in response.text


def test_archive_snapshot_header_is_exposed_to_browser_clients(engine):
    with Session(engine) as session:
        session.add(Bundle(
            id="browser-archive",
            machine_name="browser-archive",
            is_active=False,
            archived_at=datetime(2026, 9, 2, 12, 0),
            end_date_datetime=datetime(2026, 9, 1, 12, 0),
        ))
        session.commit()

    async def override_async_db():
        async_engine = create_async_engine(f"sqlite+aiosqlite:///{engine.url.database}")
        try:
            async with async_sessionmaker(
                async_engine, class_=AsyncSession, expire_on_commit=False
            )() as session:
                yield session
        finally:
            await async_engine.dispose()

    app.dependency_overrides[get_async_db] = override_async_db

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(
                "/bundles?include_inactive=true&limit=1",
                headers={"Origin": "https://hbetl.aegean-skate.ts.net"},
            )

    try:
        response = asyncio.run(exercise())
    finally:
        app.dependency_overrides.clear()

    exposed = response.headers.get("access-control-expose-headers", "")
    assert response.status_code == 200
    assert "X-Snapshot-At" in exposed
    assert response.headers.get("x-snapshot-at")
