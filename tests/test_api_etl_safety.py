"""API and CLI ETL reject unsafe source data before persistence."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import api.main as api
from api.schemas import BundleResponse
from spider.cli import run_spider
from spider.core.spider import HumbleSpider
from spider.database.models import Base, Bundle, LandingPageRawData, User
from spider.scrapers.next_page import listing_products, parse_next_page
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


def _next_listing(products):
    feed = {"feedItems": [{"value": product} for product in products]}
    stream = "a:" + json.dumps(feed) + "\n"
    return "<script>self.__next_f.push(" + json.dumps([1, stream]) + ")</script>"


def _next_detail(
    slug, *, total_items=1, extra_items=0, include_total_items=True,
):
    start = (NOW - timedelta(days=1)).replace(tzinfo=timezone.utc).isoformat()
    end = (NOW + timedelta(days=14)).replace(tzinfo=timezone.utc).isoformat()
    bundle_items = [{
        "key": f"{slug}_book",
        "title": f"{slug} Book",
    }]
    bundle_items.extend(
        {
            "key": f"{slug}_extra_{index}",
            "title": f"{slug} Extra Book {index}",
        }
        for index in range(extra_items)
    )
    bundle = {
        "slug": slug,
        "startDate": start,
        "endDate": end,
        "tiers": [{
            "sku": f"{slug}_initial",
            "price": {"amount": 5, "currencyCode": "USD"},
            "bundleItems": bundle_items,
        }],
    }
    if include_total_items:
        bundle["totalItems"] = total_items
    stream = "a:" + json.dumps(bundle) + "\n"
    return "<script>self.__next_f.push(" + json.dumps([1, stream]) + ")</script>"


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
            json_data={
                **_snapshot(count),
                "products": [{"productKey": f"new-{index}"} for index in range(count)],
            },
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
            observed_count=5,
            skipped_bundle_ids=[],
        )

    expected_operations = ["validate_scrape_quality"]
    if with_raw:
        expected_operations.append("persist_landing_page_raw_data")
    expected_operations.extend(["remove_outdated_bundles", "persist_bundles"])
    assert [call[0] for call in calls.mock_calls] == expected_operations
    assert result.bundles_processed == 5
    assert result.cleanup_ran is True
    assert result.bundles_seen == 5
    assert result.bundles_skipped == 0
    assert result.skipped_bundle_ids == []
    with Session(engine) as session:
        assert session.query(LandingPageRawData).count() == (2 if with_raw else 1)
        expired = session.get(Bundle, "expired")
        assert expired.is_active is False
        assert expired.archived_at is not None
        assert session.query(Bundle).count() == 6


@pytest.mark.parametrize("entrypoint", ["api", "cli"])
@pytest.mark.parametrize("page_kind", ["listing", "detail"])
def test_unsupported_channel_aborts_real_scrape_before_every_etl_write(
    engine, monkeypatch, entrypoint, page_kind,
):
    fixtures = Path(__file__).parent / "fixtures" / "next"
    pages = {
        "listing": (fixtures / "books.html").read_text(),
        "detail": (fixtures / "bundle.html").read_text(),
    }
    pages[page_kind] += (
        "<script>self.__next_f.push("
        + json.dumps([99, 'b:{"feedItems":[]}\n'])
        + ")</script>"
    )
    metadata = [{"machine_name": "retained-book", "authors": ["Saved Author"]}]
    with Session(engine) as session:
        # A one-item feed would pass quality validation if the extra channel
        # were ignored; rejection must come from the source parser itself.
        session.query(LandingPageRawData).one().json_data = _snapshot(1)
        retained = session.query(Bundle).one()
        retained.book_list = metadata
        retained.verification_date = NOW - timedelta(hours=1)
        session.commit()

    def respond(request):
        kind = "listing" if request.url.path == "/books" else "detail"
        return httpx.Response(200, text=pages[kind])

    target = api if entrypoint == "api" else run_spider
    writes = []
    for operation in WRITE_OPERATIONS:
        spy = Mock(wraps=getattr(target, operation))
        monkeypatch.setattr(target, operation, spy)
        writes.append(spy)

    expected = (
        "Unsupported Next.js Flight channel"
        if page_kind == "listing" else "zero bundles"
    )
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(target, "HumbleSpider", lambda: HumbleSpider(client))
        if entrypoint == "api":
            with Session(engine) as session:
                with pytest.raises(HTTPException) as failure:
                    api.trigger_etl(db=session, current_user=User(id="operator"))
                assert failure.value.status_code == 502
                assert expected in failure.value.detail
        else:
            monkeypatch.setattr(run_spider, "get_settings", Mock())
            monkeypatch.setattr(
                run_spider,
                "get_session_factory",
                lambda settings: sessionmaker(bind=engine),
            )
            with pytest.raises(SystemExit, match=expected):
                run_spider.main()

    for spy in writes:
        spy.assert_not_called()
    with Session(engine) as session:
        assert session.query(LandingPageRawData).one().id == "trusted-snapshot"
        retained = session.query(Bundle).one()
        assert retained.id == "expired"
        assert retained.book_list == metadata
        assert retained.verification_date == NOW - timedelta(hours=1)
        assert retained.is_active is True
    assert retained.archived_at is None


@pytest.mark.parametrize("entrypoint", ["api", "cli"])
def test_one_invalid_next_detail_does_not_block_other_bundle_updates(
    engine, monkeypatch, entrypoint, capsys,
):
    fixtures = Path(__file__).parent / "fixtures" / "next"
    source = listing_products(
        parse_next_page((fixtures / "books.html").read_text())
    )[0]
    good = dict(source)
    bad = dict(source)
    bad.update(
        productKey="foragingmushroomshomesteadingandmoreskyhorse_bookbundle",
        slug="foraging-mushrooms",
        url="/books/foraging-mushrooms",
        title="Foraging Mushrooms",
    )
    good_listing = _next_listing([good, bad])
    good_slug = good["url"].rsplit("/", 1)[-1]
    good_detail = _next_detail(good_slug)
    # Simulate Humble reporting more items than the detail actually contains.
    bad_detail = _next_detail(bad["slug"], total_items=2)

    with Session(engine) as session:
        trusted = session.query(LandingPageRawData).one()
        trusted.json_data = _snapshot(2)
        trusted.scraped_date = NOW - timedelta(hours=1)
        retained_failed_bundle = Bundle(
            id="retained-failed-detail",
            machine_name=bad["productKey"],
            tile_name="Previously stored Foraging bundle",
            start_date_datetime=NOW - timedelta(days=1),
            # Its last-known schedule has expired, but the current listing
            # includes it and its detail failed, so cleanup must quarantine it.
            end_date_datetime=NOW - timedelta(hours=1),
            verification_date=NOW - timedelta(hours=2),
            is_active=True,
            book_list=[{
                "machine_name": "retained-foraging-book",
                "title": "Saved Foraging Title",
                "authors": ["Saved Author"],
            }],
            price_tiers=[{"identifier": "saved-tier"}],
            scrape_warnings=[{
                "code": "total_items_overcount",
                "reported": 24,
                "parsed": 25,
                "excess": 1,
            }],
        )
        session.add(retained_failed_bundle)
        session.commit()

    def respond(request):
        if request.url.path == "/books":
            return httpx.Response(200, text=good_listing)
        if request.url.path == good["url"]:
            return httpx.Response(200, text=good_detail)
        if request.url.path == bad["url"]:
            return httpx.Response(200, text=bad_detail)
        raise AssertionError(f"Unexpected request: {request.url}")

    target = api if entrypoint == "api" else run_spider
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(target, "HumbleSpider", lambda: HumbleSpider(client))
        if entrypoint == "api":
            with Session(engine) as session:
                result = api.trigger_etl(
                    db=session,
                    current_user=User(id="operator"),
                )
            assert result.bundles_processed == 1
            assert result.bundles_seen == 2
            assert result.bundles_skipped == 1
            assert result.skipped_bundle_ids == [bad["productKey"]]
        else:
            monkeypatch.setattr(run_spider, "get_settings", Mock())
            monkeypatch.setattr(
                run_spider,
                "get_session_factory",
                lambda settings: sessionmaker(bind=engine),
            )
            run_spider.main()
            assert "Detalles omitidos (1)" in capsys.readouterr().out

    with Session(engine) as session:
        retained = session.get(Bundle, "retained-failed-detail")
        assert retained.tile_name == "Previously stored Foraging bundle"
        assert retained.book_list[0]["title"] == "Saved Foraging Title"
        assert retained.price_tiers == [{"identifier": "saved-tier"}]
        assert retained.scrape_warnings == [{
            "code": "total_items_overcount",
            "reported": 24,
            "parsed": 25,
            "excess": 1,
        }]
        assert retained.verification_date == NOW - timedelta(hours=2)
        assert retained.is_active is True
        assert retained.archived_at is None

        successful = session.query(Bundle).filter_by(
            machine_name=good["productKey"],
        ).one()
        assert successful.book_list[0]["title"] == f"{good_slug} Book"
        snapshot = session.query(LandingPageRawData).order_by(
            LandingPageRawData.scraped_date.desc(),
        ).first()
        assert snapshot.json_data["format"] == "humble-next-flight-v1"
        assert len(snapshot.json_data["products"]) == 2
        assert snapshot.json_data["detail_failures"][0]["machine_name"] == bad["productKey"]
        failed_product = next(
            item for item in snapshot.json_data["products"]
            if item["productKey"] == bad["productKey"]
        )
        assert "startDate" not in failed_product
        assert "endDate" not in failed_product

        expired = session.get(Bundle, "expired")
        assert expired.is_active is False
        assert expired.archived_at is not None
        assert session.query(LandingPageRawData).count() == 2


@pytest.mark.parametrize("entrypoint", ["api", "cli"])
def test_small_detail_overcount_is_ingested_reported_and_logged(
    engine, monkeypatch, entrypoint, caplog, capsys,
):
    fixtures = Path(__file__).parent / "fixtures" / "next"
    source = listing_products(
        parse_next_page((fixtures / "books.html").read_text())
    )[0]
    slug = source["url"].rstrip("/").rsplit("/", 1)[-1]
    listing = _next_listing([source])
    detail = _next_detail(slug, total_items=1, extra_items=1)

    with Session(engine) as session:
        trusted = session.query(LandingPageRawData).one()
        trusted.json_data = {
            "format": "humble-next-flight-v1",
            "products": [{"productKey": source["productKey"]}],
        }
        trusted.scraped_date = NOW - timedelta(hours=1)
        session.commit()

    def respond(request):
        if request.url.path == "/books":
            return httpx.Response(200, text=listing)
        if request.url.path == source["url"]:
            return httpx.Response(200, text=detail)
        raise AssertionError(f"Unexpected request: {request.url}")

    target = api if entrypoint == "api" else run_spider
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(target, "HumbleSpider", lambda: HumbleSpider(client))
        if entrypoint == "api":
            with Session(engine) as session:
                result = api.trigger_etl(
                    db=session,
                    current_user=User(id="operator"),
                )
            assert result.bundles_processed == 1
            assert result.bundles_warned == 1
            assert result.warning_bundle_ids == [source["productKey"]]
        else:
            monkeypatch.setattr(run_spider, "get_settings", Mock())
            monkeypatch.setattr(
                run_spider,
                "get_session_factory",
                lambda settings: sessionmaker(bind=engine),
            )
            run_spider.main()
            assert "Discrepancias toleradas (1)" in capsys.readouterr().out

        with Session(engine) as session:
            stored = session.query(Bundle).filter_by(
                machine_name=source["productKey"],
            ).one()
            warning = {
                "code": "total_items_overcount",
                "reported": 1,
                "parsed": 2,
                "excess": 1,
            }
            assert stored.scrape_warnings == [warning]
            assert BundleResponse.model_validate(stored).scrape_warnings == [warning]
            snapshot = session.query(LandingPageRawData).order_by(
                LandingPageRawData.scraped_date.desc(),
            ).first()
            assert snapshot.json_data["detail_warnings"] == [
                {"machine_name": source["productKey"], **warning}
            ]

        # An unavailable item count cannot verify or clear a previous warning.
        detail = _next_detail(slug, include_total_items=False)
        if entrypoint == "api":
            with Session(engine) as session:
                unverified_result = api.trigger_etl(
                    db=session,
                    current_user=User(id="operator"),
                )
            assert unverified_result.bundles_warned == 0
            assert unverified_result.warning_bundle_ids == []
        else:
            run_spider.main()
            assert "Discrepancias toleradas" not in capsys.readouterr().out

        with Session(engine) as session:
            stored = session.query(Bundle).filter_by(
                machine_name=source["productKey"],
            ).one()
            unverified_warning = {
                **warning,
                "status": "unverified",
                "current_parsed": 1,
            }
            assert stored.scrape_warnings == [unverified_warning]
            assert BundleResponse.model_validate(
                stored,
            ).scrape_warnings == [unverified_warning]
            snapshot = session.query(LandingPageRawData).order_by(
                LandingPageRawData.scraped_date.desc(),
            ).first()
            assert "detail_warnings" not in snapshot.json_data

        # A matching positive count is authoritative and clears the warning.
        detail = _next_detail(slug, total_items=1)
        if entrypoint == "api":
            with Session(engine) as session:
                clean_result = api.trigger_etl(
                    db=session,
                    current_user=User(id="operator"),
                )
            assert clean_result.bundles_warned == 0
            assert clean_result.warning_bundle_ids == []
        else:
            run_spider.main()
            assert "Discrepancias toleradas" not in capsys.readouterr().out

    assert source["productKey"] in caplog.text
    with Session(engine) as session:
        stored = session.query(Bundle).filter_by(
            machine_name=source["productKey"],
        ).one()
        warning = {
            "code": "total_items_overcount",
            "reported": 1,
            "parsed": 2,
            "excess": 1,
        }
        assert stored.scrape_warnings == []
        assert BundleResponse.model_validate(stored).scrape_warnings == []
        snapshot = session.query(LandingPageRawData).order_by(
            LandingPageRawData.scraped_date.desc(),
        ).first()
        assert "detail_warnings" not in snapshot.json_data
