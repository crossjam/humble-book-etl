from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from spider.cli import run_spider
from spider.database.models import Base, Bundle, BundleLifecycleEvent, LandingPageRawData
from spider.database.persistence import (
    ScrapeQualityError,
    backfill_lifecycle_events_from_raw_data,
    persist_bundles,
    remove_outdated_bundles,
    validate_scrape_quality,
)
from spider.schemas.bundle import BundleRecord
from spider.schemas.raw_data import LandingPageRawDataRecord


NOW = datetime(2026, 11, 12, 12, 0)


@pytest.fixture
def engine():
    test_engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


def record(name='bundle-one', **fields):
    return BundleRecord(
        machine_name=name,
        start_date_datetime=NOW - timedelta(days=2),
        end_date_datetime=NOW + timedelta(days=2),
        **fields,
    )


def snapshot_payload(count, format='legacy'):
    if format == 'next':
        return {
            'format': 'humble-next-flight-v1',
            'flight': 'full serialized flight stream',
            'products': [{'productKey': f'bundle-{index}'} for index in range(count)],
        }
    return {
        'data': {
            'books': {
                'mosaic': [{
                    'products': [{'machine_name': f'bundle-{index}'} for index in range(count)],
                }],
            },
        },
    }


def add_snapshot(session, count=10, *, when=None, payload=None, format='legacy'):
    session.add(LandingPageRawData(
        json_data=snapshot_payload(count, format) if payload is None else payload,
        scraped_date=when or NOW - timedelta(hours=1),
        source_url='https://www.humblebundle.com/books',
    ))
    session.commit()


def add_active_catalog(session, count):
    session.add_all([
        Bundle(
            machine_name=f'active-{index}',
            start_date_datetime=NOW - timedelta(days=1),
            end_date_datetime=NOW + timedelta(days=1),
            is_active=True,
        )
        for index in range(count)
    ])
    session.commit()


@pytest.mark.parametrize('explicit_none', [False, True])
def test_partial_enrichment_preserves_all_existing_optional_metadata(engine, explicit_none):
    metadata = {
        'price_tiers': [{'amount': 10}],
        'book_list': [{'title': 'Retained book'}],
        'msrp_total': 120.0,
        'raw_html': '<html>retained</html>',
        'featured_image': 'https://example.test/featured.png',
        'tile_logo': 'https://example.test/logo.png',
        'marketing_blurb': 'Retained marketing',
        'author': 'Retained author',
        'supports_partners': True,
        'type_value': 'books',
    }
    with Session(engine) as session:
        persist_bundles([record(is_active=True, **metadata)], session, now=NOW)
        original = session.query(Bundle).one()
        identity = original.id
        original_verification = original.verification_date

        partial = {key: None for key in metadata} if explicit_none else {}
        incoming = record(
            is_active=True, tile_name='Fresh title',
            verification_date=original_verification + timedelta(minutes=1),
            **partial,
        )
        persist_bundles([incoming], session, now=NOW)
        session.expire_all()

        retained = session.query(Bundle).one()
        assert retained.id == identity
        assert retained.tile_name == 'Fresh title'
        assert retained.verification_date > original_verification
        for key, value in metadata.items():
            assert getattr(retained, '_type' if key == 'type_value' else key) == value


@pytest.mark.parametrize('replacement', [
    [],
    [{'title': 'Replacement book', 'amount': 20}],
])
def test_supplied_detail_lists_replace_previous_lists_including_empty(engine, replacement):
    with Session(engine) as session:
        persist_bundles([
            record(price_tiers=[{'amount': 10}], book_list=[{'title': 'Old'}]),
        ], session, now=NOW)
        persist_bundles([
            record(price_tiers=replacement, book_list=replacement),
        ], session, now=NOW)

        retained = session.query(Bundle).one()
        assert retained.price_tiers == replacement
        assert retained.book_list == replacement


def test_supplied_false_and_zero_metadata_are_not_ignored(engine):
    with Session(engine) as session:
        persist_bundles([
            record(supports_partners=True, msrp_total=100, bundles_sold_decimal=20),
        ], session, now=NOW)
        persist_bundles([
            record(supports_partners=False, msrp_total=0, bundles_sold_decimal=0),
        ], session, now=NOW)
        retained = session.query(Bundle).one()
        assert retained.supports_partners is False
        assert retained.msrp_total == 0
        assert retained.bundles_sold_decimal == 0


def test_absent_default_active_flag_does_not_deactivate_existing_bundle(engine):
    with Session(engine) as session:
        persist_bundles([record(is_active=True)], session, now=NOW)
        persist_bundles([record()], session, now=NOW)
        assert session.query(Bundle).one().is_active is True


def test_matching_books_preserve_unknown_metadata_without_restoring_removed_items(engine):
    previous_books = [
        {
            'machine_name': 'matching',
            'title': 'Old title',
            'msrp': 25,
            'preview': {'url': 'https://example.test/preview.pdf'},
            'detail_image': 'https://example.test/detail.png',
            'authors': ['Old author'],
            'formats': ['pdf'],
        },
        {'machine_name': 'removed', 'title': 'Removed book', 'msrp': 10},
    ]
    incoming_books = [
        {'machine_name': 'new', 'title': 'New book', 'msrp': None},
        {
            'machine_name': 'matching',
            'title': 'Fresh title',
            'msrp': None,
            'preview': None,
            'authors': [],
            'formats': ['epub'],
        },
    ]
    with Session(engine) as session:
        persist_bundles([record(book_list=previous_books)], session, now=NOW)
        persist_bundles([record(book_list=incoming_books)], session, now=NOW)

        books = session.query(Bundle).one().book_list
        assert [book['machine_name'] for book in books] == ['new', 'matching']
        assert books[0]['msrp'] is None
        assert books[1]['title'] == 'Fresh title'
        assert books[1]['msrp'] == 25
        assert books[1]['preview'] == previous_books[0]['preview']
        assert books[1]['detail_image'] == previous_books[0]['detail_image']
        assert books[1]['authors'] == []
        assert books[1]['formats'] == ['epub']
        assert incoming_books[1]['msrp'] is None


def test_new_non_null_book_metadata_replaces_previous_values_including_zero(engine):
    with Session(engine) as session:
        persist_bundles([record(book_list=[
            {'machine_name': 'same', 'msrp': 25, 'preview': {'url': 'old'}},
        ])], session, now=NOW)
        persist_bundles([record(book_list=[
            {'machine_name': 'same', 'msrp': 0, 'preview': {'url': 'new'}},
        ])], session, now=NOW)
        assert session.query(Bundle).one().book_list == [
            {'machine_name': 'same', 'msrp': 0, 'preview': {'url': 'new'}},
        ]


def test_bundle_expiring_exactly_now_is_archived_and_cannot_reactivate(engine):
    expired = record(is_active=True).model_copy(update={'end_date_datetime': NOW})
    with Session(engine) as session:
        persist_bundles([expired], session, now=NOW)
        retained = session.query(Bundle).one()
        assert retained.is_active is False
        assert retained.archived_at == NOW

        retained.is_active = True
        retained.archived_at = None
        session.commit()
        remove_outdated_bundles(session, now=NOW)
        assert retained.is_active is False
        assert retained.archived_at == NOW


@pytest.mark.parametrize('format', ['legacy', 'next'])
def test_quality_gate_rejects_over_fifty_percent_loss_against_snapshot(engine, format):
    with Session(engine) as session:
        add_snapshot(session, count=10, format=format)
        with pytest.raises(ScrapeQualityError, match='loss exceeds 50%'):
            validate_scrape_quality([record(f'new-{index}') for index in range(4)], session, now=NOW)
        assert session.query(Bundle).count() == 0
        assert session.query(LandingPageRawData).count() == 1


@pytest.mark.parametrize('format', ['legacy', 'next'])
def test_quality_gate_accepts_exactly_fifty_percent_loss(engine, format):
    with Session(engine) as session:
        add_snapshot(session, count=10, format=format)
        validate_scrape_quality([record(f'new-{index}') for index in range(5)], session, now=NOW)


def test_quality_gate_uses_latest_valid_snapshot_not_historical_maximum(engine):
    with Session(engine) as session:
        add_snapshot(session, count=20, when=NOW - timedelta(days=2))
        add_snapshot(session, count=6)
        validate_scrape_quality([record(f'new-{index}') for index in range(3)], session, now=NOW)


def test_quality_gate_counts_all_legacy_mosaics(engine):
    payload = snapshot_payload(2)
    payload['data']['books']['mosaic'].append({
        'products': [{'machine_name': f'extra-{index}'} for index in range(8)],
    })
    with Session(engine) as session:
        add_snapshot(session, payload=payload)
        with pytest.raises(ScrapeQualityError, match='baseline 10'):
            validate_scrape_quality([record(), record('two')], session, now=NOW)


@pytest.mark.parametrize('bad_payload', [
    {},
    [],
    'invalid JSON',
    snapshot_payload(0),
    snapshot_payload(0, 'next'),
    {'data': {'books': None}},
    {'data': {'games': {'mosaic': [{'products': [{'machine_name': 'unrelated'}]}]}}},
    {'format': 'humble-next-flight-v1', 'products': [None]},
    {'format': 'humble-next-flight-v1', 'products': [{'productKey': ''}]},
    {'format': 'humble-next-flight-v1', 'products': [{'productKey': 'x'}, {'productKey': 'x'}]},
])
def test_bad_or_unrelated_recent_snapshots_do_not_reset_baseline(engine, bad_payload):
    with Session(engine) as session:
        add_snapshot(session, count=10, when=NOW - timedelta(days=1))
        add_snapshot(session, payload=bad_payload)
        with pytest.raises(ScrapeQualityError, match='baseline 10'):
            validate_scrape_quality([record()], session, now=NOW)


def test_quality_gate_reads_legacy_json_string_snapshots(engine):
    import json

    with Session(engine) as session:
        add_snapshot(session, payload=json.dumps(snapshot_payload(10)))
        with pytest.raises(ScrapeQualityError, match='baseline 10'):
            validate_scrape_quality([record()], session, now=NOW)


def test_quality_gate_uses_valid_active_catalog_when_no_snapshot_exists(engine):
    with Session(engine) as session:
        add_active_catalog(session, 10)
        with pytest.raises(ScrapeQualityError, match='active catalog: 10'):
            validate_scrape_quality([record()], session, now=NOW)


def test_smaller_snapshot_does_not_weaken_trusted_active_catalog_baseline(engine):
    with Session(engine) as session:
        add_active_catalog(session, 10)
        add_snapshot(session, count=2)
        with pytest.raises(ScrapeQualityError, match='baseline 10'):
            validate_scrape_quality([record()], session, now=NOW)


def test_quality_gate_does_not_count_archived_expired_inactive_or_future_rows(engine):
    excluded = [
        {'is_active': False},
        {'archived_at': NOW - timedelta(days=1)},
        {'end_date_datetime': NOW - timedelta(days=1)},
        {'end_date_datetime': NOW},
        {'start_date_datetime': NOW + timedelta(days=1)},
        {'start_date_datetime': None},
    ]
    with Session(engine) as session:
        for index, changes in enumerate(excluded):
            fields = {
                'machine_name': f'old-{index}',
                'is_active': True,
                'start_date_datetime': NOW - timedelta(days=2),
                'end_date_datetime': NOW + timedelta(days=2),
            }
            fields.update(changes)
            session.add(Bundle(**fields))
        session.commit()
        validate_scrape_quality([record()], session, now=NOW.replace(tzinfo=timezone.utc))


def test_quality_gate_accepts_bootstrap_without_baseline(engine):
    with Session(engine) as session:
        validate_scrape_quality([record()], session, now=NOW)


@pytest.mark.parametrize('records, message', [
    ([], 'zero bundles'),
    ([record(), record()], 'duplicate bundles'),
    ([record('')], 'empty bundle identity'),
])
def test_quality_gate_rejects_empty_or_duplicate_scrapes_even_without_baseline(engine, records, message):
    with Session(engine) as session:
        with pytest.raises(ScrapeQualityError, match=message):
            validate_scrape_quality(records, session, now=NOW)


@pytest.mark.parametrize('count', [0, 1])
def test_cli_quality_failure_precedes_snapshot_archive_and_upsert(engine, monkeypatch, count):
    with Session(engine) as session:
        add_snapshot(session, count=10)
        session.add(Bundle(
            machine_name='expired-but-not-yet-archived',
            is_active=True,
            start_date_datetime=NOW - timedelta(days=2),
            end_date_datetime=NOW - timedelta(days=1),
        ))
        session.commit()

    fake_spider = Mock()
    fake_spider.__enter__ = Mock(return_value=fake_spider)
    fake_spider.__exit__ = Mock(return_value=False)
    fake_spider.fetch_bundles.return_value = [record(f'new-{index}') for index in range(count)]
    fake_spider.get_raw_data_record.return_value = LandingPageRawDataRecord(
        json_data=snapshot_payload(count),
        source_url='https://www.humblebundle.com/books',
        scraped_date=NOW,
    )
    monkeypatch.setattr(run_spider, 'HumbleSpider', Mock(return_value=fake_spider))
    monkeypatch.setattr(run_spider, 'get_settings', Mock())
    monkeypatch.setattr(run_spider, 'get_session_factory', lambda settings: sessionmaker(bind=engine))
    writes = []
    for operation in ('persist_landing_page_raw_data', 'remove_outdated_bundles', 'persist_bundles'):
        spy = Mock()
        monkeypatch.setattr(run_spider, operation, spy)
        writes.append(spy)

    with pytest.raises(SystemExit, match='Error de calidad'):
        run_spider.main()

    for spy in writes:
        spy.assert_not_called()
    with Session(engine) as session:
        assert session.query(LandingPageRawData).count() == 1
        assert session.query(Bundle).one().is_active is True
        assert session.query(Bundle).one().archived_at is None


def test_lifecycle_replay_supports_legacy_to_next_snapshot_transition(engine):
    legacy = snapshot_payload(1)
    legacy['data']['books']['mosaic'][0]['products'][0].update({
        'start_date|datetime': '2026-11-01T18:00:00Z',
        'end_date|datetime': '2026-11-15T18:00:00Z',
    })
    next_payload = snapshot_payload(1, 'next')
    next_payload['products'][0].update({
        'name': 'Next bundle title',
        'startDate': '2026-11-01T18:00:00Z',
        'endDate': '2026-11-20T18:00:00Z',
    })
    with Session(engine) as session:
        add_snapshot(session, payload=legacy, when=NOW - timedelta(days=2))
        add_snapshot(session, payload=next_payload, when=NOW - timedelta(days=1))

        assert backfill_lifecycle_events_from_raw_data(session) == 1
        assert backfill_lifecycle_events_from_raw_data(session) == 0
        event = session.query(BundleLifecycleEvent).one()
        assert event.event_type == 'extended'
        assert event.bundle_title == 'Next bundle title'
        assert event.previous_end_at == datetime(2026, 11, 15, 18)
        assert event.new_end_at == datetime(2026, 11, 20, 18)


def test_lifecycle_replay_does_not_invent_dates_for_unenriched_next_listing(engine):
    with Session(engine) as session:
        add_snapshot(session, payload=snapshot_payload(1, 'next'), when=NOW - timedelta(days=2))
        add_snapshot(session, payload=snapshot_payload(1, 'next'), when=NOW - timedelta(days=1))
        assert backfill_lifecycle_events_from_raw_data(session) == 0


def test_lifecycle_replay_retains_known_dates_across_unenriched_observation(engine):
    first = snapshot_payload(1, 'next')
    first['products'][0].update({
        'startDate': '2026-11-01T18:00:00Z',
        'endDate': '2026-11-15T18:00:00Z',
    })
    last = snapshot_payload(1, 'next')
    last['products'][0].update({
        'startDate': '2026-11-01T18:00:00Z',
        'endDate': '2026-11-20T18:00:00Z',
    })
    with Session(engine) as session:
        add_snapshot(session, payload=first, when=NOW - timedelta(days=3))
        add_snapshot(session, payload=snapshot_payload(1, 'next'), when=NOW - timedelta(days=2))
        add_snapshot(session, payload=last, when=NOW - timedelta(days=1))

        assert backfill_lifecycle_events_from_raw_data(session) == 1
        assert session.query(BundleLifecycleEvent).one().event_type == 'extended'
