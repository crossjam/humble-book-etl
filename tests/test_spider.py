import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from spider.core.errors import HumbleSpiderError
from spider.core.spider import HumbleSpider
from spider.scrapers.next_page import listing_products, parse_next_page
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


def next_listing_html(products):
    feed = {'feedItems': [{'value': product} for product in products]}
    stream = 'a:' + json.dumps(feed) + '\n'
    return '<script>self.__next_f.push(' + json.dumps([1, stream]) + ')</script>'


def next_detail_html(slug):
    bundle = {
        'slug': slug,
        'startDate': '2026-11-01T00:00:00Z',
        'endDate': '2026-12-01T00:00:00Z',
        'totalItems': 1,
        'tiers': [{
            'sku': f'{slug}_initial',
            'price': {'amount': 5, 'currencyCode': 'USD'},
            'bundleItems': [{
                'key': f'{slug}_book',
                'title': f'{slug} Book',
            }],
        }],
    }
    stream = 'a:' + json.dumps(bundle) + '\n'
    return '<script>self.__next_f.push(' + json.dumps([1, stream]) + ')</script>'


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


def test_successful_next_snapshot_uses_detail_dates_not_listing_aliases():
    source = listing_products(
        parse_next_page((FIXTURES / 'books.html').read_text())
    )[0]
    source['start_date|datetime'] = '2020-01-01T00:00:00Z'
    source['end_date|datetime'] = '2099-01-01T00:00:00Z'
    records, raw = run_spider(
        next_listing_html([source]),
        (FIXTURES / 'bundle.html').read_text(),
    )

    product = raw.json_data['products'][0]
    assert product['startDate'] == records[0].start_date_datetime.isoformat()
    assert product['endDate'] == records[0].end_date_datetime.isoformat()
    assert 'start_date|datetime' not in product
    assert 'end_date|datetime' not in product


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
def test_new_listing_records_detail_failure_without_losing_the_listing_snapshot(detail):
    records, raw = run_spider((FIXTURES / 'books.html').read_text(), detail)

    assert records == []
    assert raw.json_data['format'] == 'humble-next-flight-v1'
    assert len(raw.json_data['products']) == 1
    assert raw.json_data['detail_failures'][0]['machine_name'] == (
        'datascienceoreilly_bookbundle'
    )
    # Failed details must not invent or replay schedule dates from the listing.
    assert 'startDate' not in raw.json_data['products'][0]
    assert 'endDate' not in raw.json_data['products'][0]


def test_one_next_detail_failure_skips_only_that_bundle_and_is_auditable():
    fixture = (FIXTURES / 'books.html').read_text()
    source_product = listing_products(parse_next_page(fixture))[0]
    failed_product = copy.deepcopy(source_product)
    failed_product.update(
        productKey='second_bundle_bookbundle',
        slug='second-bundle',
        url='/books/second-bundle',
        title='Second bundle',
    )
    failed_product['start_date|datetime'] = '2020-01-01T00:00:00Z'
    failed_product['end_date|datetime'] = '2099-01-01T00:00:00Z'
    listing = next_listing_html([source_product, failed_product])
    detail_html = (FIXTURES / 'bundle.html').read_text()

    def respond(request):
        if request.url.path == '/books':
            return httpx.Response(200, text=listing)
        if request.url.path == '/books/data-science-oreilly-books':
            return httpx.Response(200, text=detail_html)
        return httpx.Response(200, text='<title>Detail temporarily unavailable</title>')

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with HumbleSpider(client) as spider:
            records = spider.fetch_bundles()
            raw = spider.get_raw_data_record()

    assert [record.machine_name for record in records] == [
        'datascienceoreilly_bookbundle',
    ]
    assert len(raw.json_data['products']) == 2
    assert raw.json_data['products'][0]['startDate'] == (
        records[0].start_date_datetime.isoformat()
    )
    assert 'startDate' not in raw.json_data['products'][1]
    assert 'endDate' not in raw.json_data['products'][1]
    assert 'start_date|datetime' not in raw.json_data['products'][1]
    assert 'end_date|datetime' not in raw.json_data['products'][1]
    assert raw.json_data['detail_failures'] == spider.detail_failures == [
        {
            'machine_name': 'second_bundle_bookbundle',
            'reason': 'No Next.js Flight data found',
        },
    ]


@pytest.mark.parametrize('malformed_description', [
    ['unexpected', 'list'],
    {'unexpected': 'dict'},
])
def test_one_malformed_optional_listing_field_does_not_abort_other_bundles(
    malformed_description,
):
    fixture = (FIXTURES / 'books.html').read_text()
    source_product = listing_products(parse_next_page(fixture))[0]
    good_product = copy.deepcopy(source_product)
    bad_product = copy.deepcopy(source_product)
    bad_product.update(
        productKey='bad_optional_metadata_bookbundle',
        slug='bad-optional-metadata',
        url='/books/bad-optional-metadata',
        title='Bad optional metadata',
        description=malformed_description,
    )
    listing = next_listing_html([good_product, bad_product])

    def respond(request):
        if request.url.path == '/books':
            return httpx.Response(200, text=listing)
        slug = request.url.path.rsplit('/', 1)[-1]
        return httpx.Response(200, text=next_detail_html(slug))

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with HumbleSpider(client) as spider:
            records = spider.fetch_bundles()
            raw = spider.get_raw_data_record()

    assert [record.machine_name for record in records] == [
        source_product['productKey'],
    ]
    assert raw.json_data['detail_failures'][0]['machine_name'] == (
        'bad_optional_metadata_bookbundle'
    )
    assert 'marketing_blurb' in raw.json_data['detail_failures'][0]['reason']


def test_non_string_next_logo_is_quarantined_instead_of_stringified():
    fixture = (FIXTURES / 'books.html').read_text()
    source_product = listing_products(parse_next_page(fixture))[0]
    good_product = copy.deepcopy(source_product)
    bad_product = copy.deepcopy(source_product)
    bad_product.update(
        productKey='bad_logo_metadata_bookbundle',
        slug='bad-logo-metadata',
        url='/books/bad-logo-metadata',
        title='Bad logo metadata',
        titleImageUrl={'unexpected': 'not a URL'},
    )
    listing = next_listing_html([good_product, bad_product])

    def respond(request):
        if request.url.path == '/books':
            return httpx.Response(200, text=listing)
        slug = request.url.path.rsplit('/', 1)[-1]
        return httpx.Response(200, text=next_detail_html(slug))

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with HumbleSpider(client) as spider:
            records = spider.fetch_bundles()
            raw = spider.get_raw_data_record()

    assert [record.machine_name for record in records] == [
        source_product['productKey'],
    ]
    failure = raw.json_data['detail_failures'][0]
    assert failure['machine_name'] == 'bad_logo_metadata_bookbundle'
    assert 'tile_logo' in failure['reason']


def test_unexpected_detail_parser_exception_is_quarantined_per_next_bundle():
    fixture = (FIXTURES / 'books.html').read_text()
    source_product = listing_products(parse_next_page(fixture))[0]
    good_product = copy.deepcopy(source_product)
    bad_product = copy.deepcopy(source_product)
    bad_product.update(
        productKey='malformed_legacy_detail_bookbundle',
        slug='malformed-legacy-detail',
        url='/books/malformed-legacy-detail',
        title='Malformed legacy detail',
    )
    listing = next_listing_html([good_product, bad_product])
    malformed_legacy = {
        'bundleData': {
            'tier_pricing_data': {
                'tier': {'price|money': {'amount': 5, 'currency': 'USD'}},
            },
            'tier_display_data': {
                'tier': {'header': 'Tier', 'tier_item_machine_names': ['book']},
            },
            'tier_item_data': {
                'book': {
                    'human_name': 'Book',
                    'resolved_paths': ['not', 'an', 'object'],
                },
            },
            'basic_data': {},
        },
    }
    legacy_detail = (
        '<script id="webpack-bundle-page-data" type="application/json">'
        + json.dumps(malformed_legacy)
        + '</script>'
    )

    def respond(request):
        if request.url.path == '/books':
            return httpx.Response(200, text=listing)
        if request.url.path == good_product['url']:
            return httpx.Response(
                200, text=next_detail_html(good_product['slug']),
            )
        return httpx.Response(200, text=legacy_detail)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with HumbleSpider(client) as spider:
            records = spider.fetch_bundles()
            raw = spider.get_raw_data_record()

    assert [record.machine_name for record in records] == [
        source_product['productKey'],
    ]
    failure = raw.json_data['detail_failures'][0]
    assert failure['machine_name'] == 'malformed_legacy_detail_bookbundle'
    assert 'has no attribute' in failure['reason']


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
