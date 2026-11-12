import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from spider.core.errors import HumbleSpiderError
from spider.core.spider import HumbleSpider
from spider.utils.transformers import compute_is_active

FIXTURES = Path(__file__).parent / 'fixtures' / 'next'


def legacy_listing(products):
    payload = {'data': {'books': {'mosaic': [{'products': products}]}}}
    return '<script id="landingPage-json-data">' + json.dumps(payload) + '</script>'


def legacy_product(**changes):
    return {
        'machine_name': 'example_bookbundle',
        'product_url': '/books/example',
        'start_date|datetime': '2026-11-01T00:00:00Z',
        'end_date|datetime': '2026-12-01T00:00:00Z',
        'tile_name': 'Example',
        **changes,
    }


def run_spider(listing, detail=''):
    def respond(request):
        html = listing if request.url.path == '/books' else detail
        return httpx.Response(200, text=html)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with HumbleSpider(client) as spider:
            records = spider.fetch_bundles()
            return records, spider.get_raw_data_record()


def test_captured_next_listing_and_detail_pipeline():
    records, raw = run_spider(
        (FIXTURES / 'books.html').read_text(),
        (FIXTURES / 'bundle.html').read_text(),
    )
    assert len(records) == 1
    bundle = records[0]
    assert bundle.machine_name == 'datascienceoreilly_bookbundle'
    assert bundle.start_date_datetime == datetime(2026, 10, 26, 18, tzinfo=timezone.utc)
    assert bundle.end_date_datetime == datetime(2026, 11, 16, 19, tzinfo=timezone.utc)
    assert len(bundle.book_list) == 25
    assert [tier['price']['amount'] for tier in bundle.price_tiers] == [5, 18, 30]
    assert bundle.msrp_total == 1597
    assert bundle.featured_image
    assert raw.json_version == 'humble-next-flight-v1'
    product = raw.json_data['products'][0]
    assert product['startDate'] == bundle.start_date_datetime.isoformat()
    assert product['endDate'] == bundle.end_date_datetime.isoformat()
    assert 'flight' in raw.json_data
    assert raw.json_hash


def test_legacy_listing_still_works_and_missing_details_stay_unset():
    records, raw = run_spider(legacy_listing([legacy_product()]))
    assert records[0].book_list is None
    assert 'book_list' not in records[0].model_fields_set
    assert records[0].end_date_datetime.month == 12
    assert raw.json_version == 'humble-landing-v1'


def test_legacy_multiple_mosaics_are_not_silently_truncated():
    payload = {'data': {'books': {'mosaic': [
        {'products': [legacy_product()]},
        {'products': [legacy_product(machine_name='other', product_url='/books/other')]},
    ]}}}
    html = '<script id="landingPage-json-data">' + json.dumps(payload) + '</script>'
    records, _ = run_spider(html)
    assert len(records) == 2


@pytest.mark.parametrize('detail', [
    '<title>Access denied</title>',
    '<script>self.__next_f.push([1,"a:{}\\n"])</script>',
    '<script id="webpack-bundle-page-data" type="application/json">{}</script>',
])
def test_new_listing_never_succeeds_without_parsable_details(detail):
    with pytest.raises(HumbleSpiderError, match='Detalle incompleto'):
        run_spider((FIXTURES / 'books.html').read_text(), detail)


@pytest.mark.parametrize('html', [
    '<title>Unexpected landing page</title>',
    '<script id="landingPage-json-data">{broken}</script>',
    '<script id="landingPage-json-data">[]</script>',
    legacy_listing([]),
    legacy_listing([legacy_product(), legacy_product()]),
])
def test_invalid_or_empty_catalog_is_an_error(html):
    with pytest.raises(HumbleSpiderError):
        run_spider(html)


@pytest.mark.parametrize('changes', [
    {'start_date|datetime': None},
    {'end_date|datetime': 'invalid'},
    {'start_date|datetime': '2026-12-02T00:00:00Z'},
    {'tile_name': 'x' * 256},
])
def test_invalid_records_fail_entire_run_not_success_with_discarded_rows(changes):
    with pytest.raises(HumbleSpiderError, match='Bundle inválido'):
        run_spider(legacy_listing([legacy_product(**changes)]))


def test_nontext_upstream_field_is_a_controlled_scrape_error():
    with pytest.raises(HumbleSpiderError, match='normalizar'):
        run_spider(legacy_listing([legacy_product(tile_name=123)]))


def test_sale_period_is_end_exclusive():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    end = datetime(2026, 11, 2, tzinfo=timezone.utc)
    assert compute_is_active(start, end, now=start)
    assert not compute_is_active(start, end, now=end)
