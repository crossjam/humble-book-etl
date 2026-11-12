"""Read Humble's embedded Next.js Flight data without executing JavaScript.

This is deliberately a small, versioned *source adapter*, not a general React
runtime. Model rows and UTF-8 length-prefixed text rows are sufficient for the
public catalog captured on 2026-11-12. Unknown framing fails closed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterator

from bs4 import BeautifulSoup

FORMAT = 'humble-next-flight-v1'
# Validate the initializer's push as well as subsequent direct pushes.
_PUSH = re.compile(
    r'(?:self\.__next_f|\(\s*self\.__next_f\s*=\s*self\.__next_f\s*\|\|\s*\[\s*\]\s*\))'
    r'\.push\(\s*'
)
_HEADER = re.compile(rb'([0-9a-f]*):')
_REFERENCE = re.compile(r'^\$([0-9a-f]+)((?::[^:]+)*)$')
_MAX_HTML_CHARS = 10_000_000
_MAX_NODES = 250_000
_MAX_DEPTH = 100


@dataclass
class NextPage:
    flight: str
    rows: dict[str, Any]
    text_rows: frozenset[str] = frozenset()

    def objects(self) -> Iterator[dict]:
        """Walk model data, not arbitrary text or JavaScript source."""
        stack = [(value, 0) for value in self.rows.values()]
        remaining = _MAX_NODES
        while stack:
            value, depth = stack.pop()
            remaining -= 1
            if remaining < 0 or depth > _MAX_DEPTH:
                raise ValueError('Flight model exceeds traversal budget')
            if isinstance(value, dict):
                yield value
                stack.extend((child, depth + 1) for child in value.values())
            elif isinstance(value, list):
                stack.extend((child, depth + 1) for child in value)

    def resolve(
        self, value: Any, _seen: frozenset = frozenset(),
        _budget: list[int] | None = None, _depth: int = 0,
    ) -> Any:
        """Resolve data references only; never load client components or code."""
        budget = [_MAX_NODES] if _budget is None else _budget
        budget[0] -= 1
        if budget[0] < 0 or _depth > _MAX_DEPTH:
            raise ValueError('Flight reference exceeds expansion budget')
        def resolve(child, seen=_seen):
            return self.resolve(child, seen, budget, _depth + 1)

        if isinstance(value, dict):
            return {key: resolve(child) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve(child) for child in value]
        if isinstance(value, str):
            if value == '$undefined':
                return None
            if value.startswith('$$'):
                return value[1:]
            match = _REFERENCE.fullmatch(value)
            if match:
                key, path = match.groups()
                if key not in self.rows or value in _seen:
                    raise ValueError(f'Unresolved or cyclic Flight data reference: {value}')
                seen = _seen | {value}
                target = self.rows[key]
                for part in path.split(':')[1:]:
                    if isinstance(target, str) and _REFERENCE.fullmatch(target):
                        target = resolve(target, seen)
                    try:
                        if isinstance(target, list):
                            # Flight serializes React elements as ["$", type,
                            # key, props]; outlined references use named props.
                            index = 3 if part == 'props' and target[:1] == ['$'] else int(part)
                            target = target[index]
                        else:
                            target = target[part]
                    except (KeyError, IndexError, TypeError, ValueError) as exc:
                        raise ValueError(f'Invalid Flight data path: {value}') from exc
                # T rows contain literal text, not another encoded model value.
                if not path and key in self.text_rows:
                    return target
                return resolve(target, seen)
        return value


def parse_next_page(html: str) -> NextPage:
    """Accept bootstrap 0 and text 1 only; reject other channels before decoding."""
    if len(html) > _MAX_HTML_CHARS:
        raise ValueError('Next.js page exceeds source size budget')
    frames = []
    decoder = json.JSONDecoder()
    soup = BeautifulSoup(html, 'html.parser')
    for script in soup.find_all('script'):
        if script.get('src'):
            continue
        text = script.string or ''
        offset = 0
        while match := _PUSH.search(text, offset):
            try:
                frame, end = decoder.raw_decode(text, match.end())
            except (json.JSONDecodeError, RecursionError) as exc:
                raise ValueError('Invalid Next.js Flight frame') from exc
            if not text[end:].lstrip().startswith(')'):
                raise ValueError('Non-JSON Next.js Flight frame')
            # Do not search for push() text inside the JSON we just consumed.
            offset = end + 1
            if (
                not isinstance(frame, list)
                or not frame
                or type(frame[0]) is not int
            ):
                raise ValueError('Invalid Next.js Flight channel')
            if frame[0] == 0:
                if len(frame) != 1:
                    raise ValueError('Invalid Next.js Flight bootstrap channel')
            elif frame[0] == 1:
                if len(frame) != 2 or not isinstance(frame[1], str):
                    raise ValueError('Invalid Next.js Flight text channel')
                frames.append(frame[1])
            else:
                raise ValueError(f'Unsupported Next.js Flight channel: {frame[0]}')
    if not frames:
        raise ValueError('No Next.js Flight data found')

    flight = ''.join(frames)
    data = flight.encode('utf-8')
    offset = 0
    rows = {}
    text_rows = set()
    while offset < len(data):
        match = _HEADER.match(data, offset)
        if not match:
            raise ValueError(f'Unsupported Flight record at byte {offset}')
        key = match[1].decode('ascii')
        offset = match.end()
        if data[offset:offset + 1] == b'T':
            comma = data.find(b',', offset)
            if comma < 0 or not re.fullmatch(rb'T[0-9a-f]+', data[offset:comma]):
                raise ValueError('Invalid Flight text length')
            length = int(data[offset + 1:comma], 16)
            offset = comma + 1
            if offset + length > len(data):
                raise ValueError('Truncated Flight text record')
            value = data[offset:offset + length].decode('utf-8')
            offset += length
            text_rows.add(key)
        else:
            end = data.find(b'\n', offset)
            if end < 0:
                raise ValueError('Truncated Flight model record')
            raw = data[offset:end].decode('utf-8')
            offset = end + 1
            # Imports, resource hints, debug/error rows are framework metadata,
            # never a source of catalog objects.
            if raw in ('X', 'C') or raw.startswith(('I', 'HL', 'D', 'W', 'E', 'N')):
                continue
            try:
                value = json.loads(raw)
            except (json.JSONDecodeError, RecursionError) as exc:
                raise ValueError('Unsupported Flight model record') from exc
        if not key or key in rows:
            raise ValueError('Missing or duplicate Flight data identifier')
        rows[key] = value
    return NextPage(flight, rows, frozenset(text_rows))


def listing_products(page: NextPage) -> list[dict]:
    """Extract every books feed, deduplicating repeated layout references."""
    products = {}
    found = False
    for obj in page.objects():
        if 'feedItems' not in obj:
            continue
        found = True
        feed = page.resolve(obj['feedItems'])
        if not isinstance(feed, list):
            raise ValueError('Invalid books feed')
        for entry in feed:
            if not isinstance(entry, dict) or not isinstance(entry.get('value'), dict):
                raise ValueError('Invalid books feed entry')
            value = entry['value']
            if value.get('category') != 'books':
                continue
            key = value.get('productKey')
            if not isinstance(key, str) or not key or not value.get('endDate'):
                raise ValueError('Book bundle is missing identity or end date')
            if not isinstance(value.get('title'), str) or not value['title'].strip():
                raise ValueError('Book bundle is missing its title')
            if not isinstance(value.get('url'), str) or not value['url'].startswith('/books/'):
                raise ValueError('Invalid book bundle URL')
            if key in products and products[key] != value:
                raise ValueError(f'Conflicting book bundle identity: {key}')
            products[key] = value
    if not found or not products:
        raise ValueError('No book bundles found in Next.js data')
    return list(products.values())


def normalize_listing_product(product: dict) -> dict:
    """Map the new public feed onto our existing bundle schema."""
    return {
        'machine_name': product['productKey'],
        'product_url': product['url'],
        'tile_name': product.get('title'),
        'tile_short_name': product.get('title'),
        'tile_image': product.get('thumbnail'),
        'high_res_tile_image': product.get('thumbnail'),
        'tile_logo': product.get('titleImageUrl'),
        'category': product.get('category'),
        'end_date|datetime': product['endDate'],
        # startDate comes from the detail page, never inferred from the slug.
        'start_date|datetime': product.get('startDate'),
        'bundles_sold|decimal': product.get('numSold'),
        'marketing_blurb': product.get('description'),
        'type': product.get('productType'),
    }
