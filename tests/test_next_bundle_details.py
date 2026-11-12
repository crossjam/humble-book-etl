"""Reduced real-shaped Next.js bundle data; no network or JavaScript runtime."""
import copy
import json

import httpx
import pytest

from spider.scrapers.bundle_detail_scraper import BundleDetailScraper


SLUG = 'data-science-oreilly-books'
FIRST_KEY = 'semanticmodelingfordata_datascienceoreilly_bookbundle'
SECOND_KEY = 'datascienceonthegooglecloudplatform2ndedition_datascienceoreilly_bookbundle'
FIRST_SKU = 'datascienceoreilly_bookbundle_initial'
SECOND_SKU = 'datascienceoreilly_bookbundle_bt18'
FIRST_REF = '$e:0:props:children:props:bundlePage:tiers:0:bundleItems:0'


def next_bundle():
    """Keep source field names, actual identities, and cumulative tier references."""
    return {
        'slug': SLUG,
        'startDate': '2026-10-26T18:00:00.000Z',
        'endDate': '2026-11-16T19:00:00.000Z',
        'headline': 'Demystify data science with elite eBooks',
        'description': '$undefined',
        'details': '$6e',
        'totalItems': 2,
        'value': {'amount': 1597, 'currencyCode': 'USD', 'fractionDigits': 2},
        'logoImageUrl': 'https://images.example/logo.png',
        'thumbnailImageUrl': 'https://images.example/bundle.jpg',
        'tiers': [
            {
                'id': '1',
                'sku': FIRST_SKU,
                'price': {'amount': 5, 'currencyCode': 'USD'},
                'bundleItems': [{
                    'key': FIRST_KEY,
                    'title': 'Semantic Modeling for Data',
                    'description': '$6f',
                    'developers': [
                        {'name': 'Panos Alexopoulos, Another Author'},
                        {'name': 'Panos Alexopoulos'},
                    ],
                    'publishers': [{'name': "O'Reilly", 'url': 'https://www.oreilly.com/'}],
                    'platforms': ['pdf', 'epub', 'pdf'],
                    'thumbnailImageUrl': 'https://images.example/book.png',
                    'previewPdf': {'url': 'https://images.example/preview.pdf', 'size': '244 KB'},
                    'msrp': '$undefined',
                    'bundleType': 'books',
                }],
            },
            {
                'id': '2',
                'sku': SECOND_SKU,
                'price': {'amount': 18, 'currencyCode': 'USD', 'fractionDigits': 2},
                'bundleItems': [
                    {
                        'key': SECOND_KEY,
                        'title': 'Data Science on the Google Cloud Platform',
                        'description': '$70',
                        'msrp': {'amount': '29.99', 'currencyCode': 'USD'},
                    },
                    FIRST_REF,
                    FIRST_REF,
                ],
            },
        ],
    }


def flight_html(bundle, *, recommendations=None):
    # A React element stores props in slot 3, exposed as ":props" in Flight paths.
    root = [['$', '$L20', None, {
        'children': ['$', '$L21', None, {
            'recommendations': recommendations or [],
            'bundlePage': bundle,
        }],
    }]]
    flight = 'e:' + json.dumps(root) + '\n'
    for key, text in (
        ('6e', '<p>Real bundle details.</p>'),
        ('6f', '<p>Semantic data — café.</p><p>Readable &amp; useful.</p>'),
        ('70', '<p>Cloud platform guide.</p>'),
    ):
        # Flight text framing counts UTF-8 bytes, not Unicode code points.
        flight += f'{key}:T{len(text.encode("utf-8")):x},{text}'
    midpoint = len(flight) // 2
    return ''.join(
        '<script>self.__next_f.push(' + json.dumps([1, part]) + ')</script>'
        for part in (flight[:midpoint], flight[midpoint:])
    )


def fetch(html, path=f'/books/{SLUG}'):
    def respond(request):
        assert request.url.path.rstrip('/') == f'/books/{SLUG}'
        return httpx.Response(200, text=html)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        return BundleDetailScraper(client).fetch_bundle_details(path)


def test_next_details_normalize_metadata_and_deduplicate_cumulative_membership():
    html = flight_html(next_bundle())
    details = fetch(html)

    assert details is not None
    assert details.raw_html == html
    assert details.start_date == '2026-10-26T18:00:00.000Z'
    assert details.end_date == '2026-11-16T19:00:00.000Z'
    assert details.featured_image == 'https://images.example/bundle.jpg'
    assert details.tile_logo == 'https://images.example/logo.png'
    assert details.marketing_blurb == 'Demystify data science with elite eBooks'
    assert details.msrp_total == 1597.0  # amount is dollars despite fractionDigits=2
    assert [tier['identifier'] for tier in details.price_tiers] == [FIRST_SKU, SECOND_SKU]
    assert details.price_tiers[0]['items'] == [FIRST_KEY]
    assert details.price_tiers[1]['items'] == [SECOND_KEY, FIRST_KEY]
    assert details.price_tiers[1]['price'] == {'amount': 18.0, 'currency': 'USD'}
    assert details.book_list == [
        {
            'machine_name': FIRST_KEY,
            'title': 'Semantic Modeling for Data',
            'authors': ['Panos Alexopoulos', 'Another Author'],
            'publishers': [{'name': "O'Reilly", 'url': 'https://www.oreilly.com/'}],
            'description': 'Semantic data — café. Readable & useful.',
            'msrp': None,
            'preview': {'url': 'https://images.example/preview.pdf', 'size': '244 KB'},
            'image': 'https://images.example/book.png',
            'detail_image': None,
            'content_type': 'books',
            'formats': ['pdf', 'epub'],
            'tiers': [FIRST_SKU, SECOND_SKU],
        },
        {
            'machine_name': SECOND_KEY,
            'title': 'Data Science on the Google Cloud Platform',
            'authors': None,
            'publishers': None,
            'description': 'Cloud platform guide.',
            'msrp': 29.99,
            'preview': None,
            'image': None,
            'detail_image': None,
            'content_type': None,
            'formats': None,
            'tiers': [SECOND_SKU],
        },
    ]


def test_next_details_match_requested_slug_not_first_recommendation():
    wrong = copy.deepcopy(next_bundle())
    wrong['slug'] = 'different-recommendation-books'
    wrong['value']['amount'] = 9999
    details = fetch(
        flight_html(next_bundle(), recommendations=[wrong]),
        f'https://www.humblebundle.com/books/{SLUG}/?partner=example',
    )

    assert details is not None
    assert details.msrp_total == 1597


def test_next_details_do_not_use_a_different_bundle():
    bundle = next_bundle()
    bundle['slug'] = 'different-recommendation-books'
    assert fetch(flight_html(bundle)) is None


def test_next_details_fall_back_to_actual_tier_id_without_sku():
    bundle = next_bundle()
    del bundle['tiers'][0]['sku']
    details = fetch(flight_html(bundle))
    assert details is not None
    assert details.price_tiers[0]['identifier'] == '1'
    assert details.book_list[0]['tiers'] == ['1', SECOND_SKU]


@pytest.mark.parametrize(
    'mutate',
    [
        lambda bundle: bundle.update(tiers=[]),
        lambda bundle: bundle.update(tiers={}),
        lambda bundle: bundle.update(tiers=['not an object']),
        lambda bundle: bundle.update(startDate=None),
        lambda bundle: bundle.update(startDate='not a date'),
        lambda bundle: bundle.update(startDate='2026-10-26T18:00:00'),
        lambda bundle: bundle.update(endDate='2026-11-16'),
        lambda bundle: bundle.update(endDate=123),
        lambda bundle: bundle.pop('endDate'),
        lambda bundle: bundle['tiers'][0].update(bundleItems=[]),
        lambda bundle: bundle['tiers'][0].update(bundleItems=['$deadbeef']),
        lambda bundle: bundle['tiers'][0].update(sku=None, id=None),
        lambda bundle: bundle['tiers'][1].update(sku=FIRST_SKU),
        lambda bundle: bundle['tiers'][0]['bundleItems'][0].pop('key'),
        lambda bundle: bundle['tiers'][0]['bundleItems'][0].pop('title'),
        lambda bundle: bundle['tiers'][0]['bundleItems'][0].update(title='   '),
        lambda bundle: bundle['tiers'][0]['bundleItems'][0].update(title=123),
        lambda bundle: bundle['tiers'][0]['bundleItems'][0].update(developers={}),
        lambda bundle: bundle['tiers'][0].pop('price'),
        lambda bundle: bundle['tiers'][0].update(price=None),
        lambda bundle: bundle['tiers'][0].update(price={'amount': 5}),
        lambda bundle: bundle['tiers'][0].update(price={'amount': 5, 'currencyCode': None}),
        lambda bundle: bundle['tiers'][0].update(price={'amount': 5, 'currencyCode': ''}),
        lambda bundle: bundle['tiers'][0].update(price={'amount': 5, 'currencyCode': 123}),
        lambda bundle: bundle['tiers'][0].update(price={'amount': 5, 'currencyCode': 'bad-code'}),
        lambda bundle: bundle['tiers'][0].update(price={'amount': -1, 'currencyCode': 'USD'}),
        lambda bundle: bundle['tiers'][0].update(price={'amount': 'nan', 'currencyCode': 'USD'}),
        lambda bundle: bundle['tiers'][0].update(price={'amount': 'inf', 'currencyCode': 'USD'}),
        lambda bundle: bundle.update(totalItems=3),
        lambda bundle: bundle.update(totalItems='2'),
        lambda bundle: bundle.update(totalItems=True),
    ],
)
def test_next_details_fail_closed_on_empty_or_malformed_data(mutate, caplog):
    bundle = next_bundle()
    mutate(bundle)
    assert fetch(flight_html(bundle)) is None
    assert 'Detalle Next.js inválido' in caplog.text


@pytest.mark.parametrize('html', [
    '<html><h1>No Flight data</h1></html>',
    '<script>self.__next_f.push([1,"e:{invalid}\\n"])</script>',
    '<script>self.__next_f.push([1, doNotExecute()])</script>',
])
def test_next_details_missing_or_invalid_payload_warns(html, caplog):
    assert fetch(html) is None
    assert 'Detalle Next.js inválido' in caplog.text


def test_next_details_resolve_undefined_optional_metadata_to_null():
    bundle = next_bundle()
    for key in ('thumbnailImageUrl', 'logoImageUrl', 'headline', 'value'):
        bundle[key] = '$undefined'
    item = bundle['tiers'][0]['bundleItems'][0]
    for key in ('developers', 'publishers', 'platforms', 'description', 'previewPdf'):
        item[key] = '$undefined'
    details = fetch(flight_html(bundle))

    assert details is not None
    assert details.featured_image is None
    assert details.tile_logo is None
    assert details.marketing_blurb is None
    assert details.msrp_total is None
    assert details.book_list[0]['description'] is None
    assert details.book_list[0]['preview'] is None
    assert details.book_list[0]['authors'] is None
    assert details.book_list[0]['publishers'] is None
    assert details.book_list[0]['formats'] is None


@pytest.mark.parametrize('value', [None, '$undefined', []])
def test_next_details_distinguish_absent_collections_from_explicit_empty(value):
    bundle = next_bundle()
    item = bundle['tiers'][0]['bundleItems'][0]
    for key in ('developers', 'publishers', 'platforms'):
        item[key] = value
    details = fetch(flight_html(bundle))

    assert details is not None
    expected = [] if value == [] else None
    for key in ('authors', 'publishers', 'formats'):
        assert details.book_list[0][key] == expected
        # Second item omits these fields entirely.
        assert details.book_list[1][key] is None


@pytest.mark.parametrize(('field', 'members'), [
    ('developers', [{}]),
    ('developers', [None]),
    ('developers', ['Author']),
    ('developers', [{'name': None}]),
    ('developers', [{'name': 123}]),
    ('developers', [{'name': '   '}]),
    ('developers', [{'name': ', ,'}]),
    ('developers', [{'name': 'Valid Author'}, {}]),
    ('publishers', [None]),
    ('publishers', [{}]),
    ('publishers', ['Publisher']),
    ('publishers', [{'name': 123}]),
    ('publishers', [{'name': '   '}]),
    ('publishers', [{'url': 123}]),
    ('publishers', [{'name': 'Valid Press', 'url': False}]),
    ('publishers', [{'name': 'Valid Press'}, None]),
    ('platforms', [123]),
    ('platforms', [None]),
    ('platforms', [{}]),
    ('platforms', ['']),
    ('platforms', ['   ']),
    ('platforms', ['pdf', 123]),
])
def test_next_details_reject_malformed_collection_members(field, members, caplog):
    bundle = next_bundle()
    bundle['tiers'][0]['bundleItems'][0][field] = members
    assert fetch(flight_html(bundle)) is None
    assert 'Detalle Next.js inválido' in caplog.text


@pytest.mark.parametrize(('field', 'members'), [
    ('developers', [{}]), ('publishers', [None]), ('platforms', [123]),
])
def test_next_details_validate_repeated_books_in_later_tiers(field, members):
    bundle = next_bundle()
    repeated = copy.deepcopy(bundle['tiers'][0]['bundleItems'][0])
    repeated[field] = members
    bundle['tiers'][1]['bundleItems'][1] = repeated
    assert fetch(flight_html(bundle)) is None


def test_next_details_preserve_meaningful_publisher_and_format_values():
    bundle = next_bundle()
    item = bundle['tiers'][0]['bundleItems'][0]
    item['developers'] = [{'name': '  Author One , Author Two  '}]
    item['publishers'] = [
        {'name': '  Named Press  ', 'url': None},
        {'url': ' https://example.com/press '},
    ]
    item['platforms'] = [' pdf ', 'epub', 'pdf']
    details = fetch(flight_html(bundle))

    assert details is not None
    book = details.book_list[0]
    assert book['authors'] == ['Author One', 'Author Two']
    assert book['publishers'] == [
        {'name': 'Named Press', 'url': None},
        {'name': None, 'url': 'https://example.com/press'},
    ]
    assert book['formats'] == ['pdf', 'epub']


@pytest.mark.parametrize(('field', 'members'), [
    ('developers', [{}]), ('publishers', [None]), ('platforms', [123]),
    ('developers', [{'name': 'Valid Author'}, {}]),
    ('publishers', [{'name': 'Valid Press'}, None]),
    ('platforms', ['pdf', 123]),
])
def test_invalid_metadata_aborts_api_etl_without_clearing_retained_values(
    field, members, tmp_path, monkeypatch,
):
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from fastapi import HTTPException
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    import api.main as api
    from spider.core.spider import HumbleSpider
    from spider.database.models import (
        Base, Bundle, BundleLifecycleEvent, LandingPageRawData, User,
    )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    retained_metadata = [{
        'machine_name': FIRST_KEY,
        'title': 'Retained book',
        'authors': ['Retained Author'],
        'publishers': [{'name': 'Retained Press', 'url': 'https://example.com/press'}],
        'formats': ['pdf'],
    }]
    engine = create_engine(f'sqlite:///{tmp_path / "metadata.db"}')
    Base.metadata.create_all(engine)
    bundle = next_bundle()
    bundle['tiers'][0]['bundleItems'][0][field] = members
    listing = (Path(__file__).parent / 'fixtures' / 'next' / 'books.html').read_text()

    def respond(request):
        html = listing if request.url.path == '/books' else flight_html(bundle)
        return httpx.Response(200, text=html)

    try:
        with Session(engine) as session, httpx.Client(transport=httpx.MockTransport(respond)) as client:
            # An expired stale-true row also proves cleanup never ran.
            retained = Bundle(
                machine_name='datascienceoreilly_bookbundle',
                start_date_datetime=now - timedelta(days=2),
                end_date_datetime=now - timedelta(days=1),
                verification_date=now - timedelta(hours=1),
                is_active=True,
                book_list=retained_metadata,
            )
            session.add(retained)
            session.commit()
            before_verified = retained.verification_date
            monkeypatch.setattr(api, 'HumbleSpider', lambda: HumbleSpider(client))

            with pytest.raises(HTTPException) as failure:
                api.trigger_etl(db=session, current_user=User(id='test-operator'))
            assert failure.value.status_code == 502
            session.expire_all()
            assert retained.book_list == retained_metadata
            assert retained.verification_date == before_verified
            assert retained.is_active is True
            assert retained.archived_at is None
            assert session.query(Bundle).count() == 1
            assert session.query(LandingPageRawData).count() == 0
            assert session.query(BundleLifecycleEvent).count() == 0
    finally:
        engine.dispose()


def test_next_details_allow_zero_price_and_explicit_date_offsets():
    bundle = next_bundle()
    bundle['startDate'] = '2026-10-26T14:00:00-04:00'
    bundle['endDate'] = '2026-11-16T20:00:00+01:00'
    bundle['tiers'][0]['price'] = {'centAmount': 0, 'currencyCode': 'USD'}
    details = fetch(flight_html(bundle))

    assert details is not None
    assert details.start_date == bundle['startDate']
    assert details.end_date == bundle['endDate']
    assert details.price_tiers[0]['price'] == {'amount': 0.0, 'currency': 'USD'}


@pytest.mark.parametrize('present', [False, True])
def test_next_details_total_count_can_be_unavailable(present):
    bundle = next_bundle()
    if present:
        bundle['totalItems'] = '$undefined'
    else:
        bundle.pop('totalItems')
    assert fetch(flight_html(bundle)) is not None


@pytest.mark.parametrize(
    ('money', 'expected'),
    [
        ({'amount': 30, 'currencyCode': 'USD', 'fractionDigits': 2}, 30),
        ({'centAmount': 500, 'currencyCode': 'USD', 'fractionDigits': 2}, 5),
        ({'centAmount': 500, 'currencyCode': 'USD'}, 5),
        ({'centAmount': 500, 'currencyCode': 'JPY', 'fractionDigits': 0}, 500),
        ({'centAmount': 500, 'currencyCode': 'KWD', 'fractionDigits': 3}, 0.5),
        ({'amount': '19.99', 'currency': 'USD'}, 19.99),
        ({'amount': 0, 'currencyCode': 'USD'}, 0),
        ({'amount': True, 'currencyCode': 'USD'}, None),
        ({'amount': 'nan', 'currencyCode': 'USD'}, None),
        ({'centAmount': 500, 'fractionDigits': -1}, None),
        ({'centAmount': 500, 'fractionDigits': '2'}, None),
        ('$undefined', None),
    ],
)
def test_money_does_not_confuse_major_and_minor_units(money, expected):
    assert BundleDetailScraper._safe_amount(money) == expected
