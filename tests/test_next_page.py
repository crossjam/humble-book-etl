import json
from pathlib import Path

import pytest

from spider.scrapers.next_page import (
    listing_products,
    normalize_listing_product,
    parse_next_page,
)

FIXTURES = Path(__file__).parent / 'fixtures' / 'next'


def flight_html(stream):
    return '<script>self.__next_f.push(' + json.dumps([1, stream]) + ')</script>'


def test_reduced_captured_feed_maps_stable_keys_not_slug():
    page = parse_next_page((FIXTURES / 'books.html').read_text())
    products = listing_products(page)
    assert len(products) == 1
    record = normalize_listing_product(products[0])
    assert record['machine_name'] == 'datascienceoreilly_bookbundle'
    assert record['product_url'] == '/books/data-science-oreilly-books'
    assert record['start_date|datetime'] is None
    assert record['end_date|datetime'] == '2026-11-16T19:00:00.000Z'
    assert record['tile_logo'].startswith('https://hb-prd.imgix.net/')


def test_reduced_capture_resolves_utf8_text_and_react_props_paths():
    page = parse_next_page((FIXTURES / 'bundle.html').read_text())
    raw = next(obj for obj in page.objects() if 'startDate' in obj and 'tiers' in obj)
    bundle = page.resolve(raw)
    assert [len(tier['bundleItems']) for tier in bundle['tiers']] == [3, 7, 25]
    first = bundle['tiers'][0]['bundleItems'][0]
    assert 'café, 日本語' in first['description']
    assert first in bundle['tiers'][1]['bundleItems']
    assert bundle['description'] is None


def test_escaped_dollars_are_literals_not_references():
    page = parse_next_page(flight_html('a:{"literal":"$$abc","absent":"$undefined"}\n'))
    assert page.resolve(page.rows['a']) == {'literal': '$abc', 'absent': None}


def test_push_text_inside_a_json_value_is_not_another_javascript_frame():
    value = {'description': 'Example: self.__next_f.push([not JavaScript])'}
    page = parse_next_page(flight_html('a:' + json.dumps(value) + '\n'))
    assert page.rows['a'] == value


def test_byte_counted_text_is_literal_even_when_it_looks_like_a_reference():
    page = parse_next_page(flight_html('a:T2,$b' + 'b:{"text":"$a"}\n'))
    assert page.resolve(page.rows['b']) == {'text': '$b'}


def test_deep_json_is_a_controlled_source_error():
    with pytest.raises(ValueError):
        page = parse_next_page(flight_html('a:' + '[' * 1200 + ']' * 1200 + '\n'))
        list(page.objects())


def test_traversal_and_reference_expansion_have_budgets(monkeypatch):
    import spider.scrapers.next_page as adapter

    nested = {'leaf': True}
    for _ in range(102):
        nested = {'child': nested}
    page = parse_next_page(flight_html('a:' + json.dumps(nested) + '\n'))
    with pytest.raises(ValueError, match='budget'):
        list(page.objects())
    with pytest.raises(ValueError, match='budget'):
        page.resolve(page.rows['a'])

    # A DAG can expand exponentially without containing any reference cycle.
    monkeypatch.setattr(adapter, '_MAX_NODES', 100)
    stream = '0:{"leaf":true}\n'
    for i in range(1, 12):
        stream += f'{i:x}:' + json.dumps([f'${i-1:x}', f'${i-1:x}']) + '\n'
    with pytest.raises(ValueError, match='budget'):
        parse_next_page(flight_html(stream)).resolve('$b')


@pytest.mark.parametrize('stream', [
    'not-flight',
    'a:{"unterminated":true}',
    'a:Tff,short',
    'a:Txyz,invalid',
    'a:{"ok":true}\na:{"again":true}\n',
    'a:UNSUPPORTED\n',
])
def test_malformed_or_unknown_framing_fails_closed(stream):
    with pytest.raises(ValueError):
        parse_next_page(flight_html(stream))


@pytest.mark.parametrize('html', [
    '<title>Please verify you are human</title>',
    '<script>self.__next_f.push([1, alert("never run")])</script>',
    '<script>self.__next_f.push([1, "a:{}\\n"].concat(malicious))</script>',
    '<script>self.__next_f.push([1, {}])</script>',
])
def test_no_javascript_is_evaluated(html):
    with pytest.raises(ValueError):
        parse_next_page(html)


@pytest.mark.parametrize('stream,ref', [
    ('a:"$b"\nb:"$a"\n', '$a'),
    ('a:{}\n', '$b'),
    ('a:{"items":[]}\n', '$a:items:7'),
    ('a:{"items":[]}\n', '$a:missing'),
])
def test_missing_cyclic_or_invalid_references_fail_closed(stream, ref):
    with pytest.raises(ValueError):
        parse_next_page(flight_html(stream)).resolve(ref)


def test_feeds_deduplicate_same_identity_and_reject_conflicts():
    source = listing_products(parse_next_page((FIXTURES / 'books.html').read_text()))[0]
    feed = {'feedItems': [{'value': source}, {'value': source}]}
    page = parse_next_page(flight_html('a:' + json.dumps(feed) + '\n'))
    assert len(listing_products(page)) == 1
    feed['feedItems'][1] = {'value': {**source, 'endDate': '2099-01-01'}}
    with pytest.raises(ValueError, match='Conflicting'):
        listing_products(parse_next_page(flight_html('a:' + json.dumps(feed) + '\n')))


@pytest.mark.parametrize('change', [
    {'productKey': None}, {'endDate': None}, {'url': '/store/not-a-bundle'},
    {'category': 'games'}, {'title': None}, {'title': {'unexpected': 'value'}},
])
def test_incomplete_or_nonbooks_feeds_are_not_a_catalog(change):
    source = listing_products(parse_next_page((FIXTURES / 'books.html').read_text()))[0]
    feed = {'feedItems': [{'value': {**source, **change}}]}
    with pytest.raises(ValueError):
        listing_products(parse_next_page(flight_html('a:' + json.dumps(feed) + '\n')))
