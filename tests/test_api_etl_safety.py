"""The authenticated API ETL must use the same pre-write quality gate as CLI."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import api.main as api
from spider.database.models import Base, Bundle, LandingPageRawData, User
from spider.schemas.bundle import BundleRecord
from spider.schemas.raw_data import LandingPageRawDataRecord


NOW = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
WRITE_OPERATIONS = (
    "persist_landing_page_raw_data",
    "remove_outdated_bundles",
    "persist_bundles",
)


def _snapshot(count):
    return {
        "format": "humble-next-flight-v1",
        "products": [{"productKey": f"bundle-{index}"} for index in range(count)],
    }


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api-etl.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(LandingPageRawData(
            id="trusted-snapshot",
            json_data=_snapshot(10),
            scraped_date=NOW - timedelta(hours=1),
            source_url="https://www.humblebundle.com/books",
        ))
        session.add(Bundle(
            id="expired",
            machine_name="expired-but-not-yet-archived",
            is_active=True,
            start_date_datetime=NOW - timedelta(days=2),
            end_date_datetime=NOW - timedelta(days=1),
        ))
        session.commit()
    yield engine
    engine.dispose()


def _mock_spider(monkeypatch, count, with_raw=True):
    spider = Mock()
    spider.__enter__ = Mock(return_value=spider)
    spider.__exit__ = Mock(return_value=False)
    spider.fetch_bundles.return_value = [
        BundleRecord(
            machine_name=f"new-{index}",
            start_date_datetime=NOW - timedelta(days=1),
            end_date_datetime=NOW + timedelta(days=1),
            is_active=True,
        )
        for index in range(count)
    ]
    spider.get_raw_data_record.return_value = (
        LandingPageRawDataRecord(
            json_data=_snapshot(count),
            source_url="https://www.humblebundle.com/books",
            scraped_date=NOW,
        )
        if with_raw else None
    )
    monkeypatch.setattr(api, "HumbleSpider", Mock(return_value=spider))
    return spider


@pytest.mark.parametrize("count", [0, 1])
@pytest.mark.parametrize("with_raw", [False, True])
def test_api_quality_failure_is_502_and_precedes_every_write(
    engine, monkeypatch, count, with_raw,
):
    _mock_spider(monkeypatch, count, with_raw)
    writes = []
    for operation in WRITE_OPERATIONS:
        spy = Mock()
        monkeypatch.setattr(api, operation, spy)
        writes.append(spy)

    with Session(engine) as session:
        with pytest.raises(HTTPException) as failure:
            api.trigger_etl(db=session, current_user=User(id="operator"))
        assert failure.value.status_code == 502
        assert ("zero bundles" if count == 0 else "loss exceeds 50%") in failure.value.detail
        assert not session.new
        assert not session.dirty

    for spy in writes:
        spy.assert_not_called()
    with Session(engine) as session:
        assert session.query(LandingPageRawData).one().id == "trusted-snapshot"
        retained = session.query(Bundle).one()
        assert retained.id == "expired"
        assert retained.is_active is True
        assert retained.archived_at is None


@pytest.mark.parametrize("with_raw", [False, True])
def test_api_success_validates_before_snapshot_archive_and_upsert(
    engine, monkeypatch, with_raw,
):
    spider = _mock_spider(monkeypatch, count=5, with_raw=with_raw)
    calls = Mock()
    for operation in ("validate_scrape_quality", *WRITE_OPERATIONS):
        spy = Mock(wraps=getattr(api, operation))
        monkeypatch.setattr(api, operation, spy)
        calls.attach_mock(spy, operation)

    with Session(engine) as session:
        result = api.trigger_etl(db=session, current_user=User(id="operator"))
        api.validate_scrape_quality.assert_called_once_with(
            spider.fetch_bundles.return_value,
            session,
            now=NOW if with_raw else None,
        )

    expected_operations = ["validate_scrape_quality"]
    if with_raw:
        expected_operations.append("persist_landing_page_raw_data")
    expected_operations.extend(["remove_outdated_bundles", "persist_bundles"])
    assert [call[0] for call in calls.mock_calls] == expected_operations
    assert result.bundles_processed == 5
    assert result.cleanup_ran is True
    with Session(engine) as session:
        assert session.query(LandingPageRawData).count() == (2 if with_raw else 1)
        expired = session.get(Bundle, "expired")
        assert expired.is_active is False
        assert expired.archived_at is not None
        assert session.query(Bundle).count() == 6
