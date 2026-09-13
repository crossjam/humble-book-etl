import json

import httpx

from spider.scrapers.bundle_detail_scraper import BundleDetailScraper


def test_fetch_bundle_details_accepts_an_httpx_client():
    payload = {
        'bundleData': {
            'tier_pricing_data': {},
            'tier_display_data': {},
            'tier_item_data': {
                'book-one': {'human_name': 'Book One'},
            },
            'basic_data': {'msrp|money': {'amount': 10}},
        }
    }
    html = (
        '<script id="webpack-bundle-page-data" type="application/json">'
        f'{json.dumps(payload)}'
        '</script>'
    )

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/books/example'
        return httpx.Response(200, text=html)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        scraper = BundleDetailScraper(client)
        details = scraper.fetch_bundle_details('/books/example')

    assert details is not None
    assert details.book_list[0]['title'] == 'Book One'
    assert details.msrp_total == 10.0


def test_extract_book_list_includes_detail_panel_metadata():
    tier_items = {
        'singularity_imagecomics': {
            'human_name': 'SINGULARITY',
            'developers': [
                {'developer-name': 'Mat Groom, Bear McCreary'},
                {'developer-name': 'Mat Groom'},
            ],
            'publishers': [
                {
                    'publisher-name': 'Image Comics',
                    'publisher-url': 'https://imagecomics.com/',
                }
            ],
            'description_text': (
                '<p>How much loss can one soul endure?</p>'
                '<p>A sweeping, cosmic story.</p>'
            ),
            'msrp_price': {'amount': '19.99', 'currency': 'USD'},
            'book_preview': {'preview_file_type': 'PDF'},
            'resolved_paths': {
                'front_page_art_imgix': 'https://images.example/cover.png',
                'preview_image': 'https://images.example/detail.jpg',
            },
            'item_content_type': 'ebook',
            'platforms_and_oses': {
                'ebook': {'download': ['pdf', 'epub']},
            },
        }
    }
    display = {
        'bt25': {'tier_item_machine_names': ['singularity_imagecomics']},
    }

    with BundleDetailScraper() as scraper:
        books = scraper._extract_book_list(tier_items, display)

    assert books == [
        {
            'machine_name': 'singularity_imagecomics',
            'title': 'SINGULARITY',
            'authors': ['Mat Groom', 'Bear McCreary'],
            'publishers': [
                {'name': 'Image Comics', 'url': 'https://imagecomics.com/'}
            ],
            'description': (
                'How much loss can one soul endure? A sweeping, cosmic story.'
            ),
            'msrp': 19.99,
            'preview': {'preview_file_type': 'PDF'},
            'image': 'https://images.example/cover.png',
            'detail_image': 'https://images.example/detail.jpg',
            'content_type': 'ebook',
            'formats': ['pdf', 'epub'],
            'tiers': ['bt25'],
        }
    ]


def test_extract_book_list_uses_empty_detail_collections_when_absent():
    with BundleDetailScraper() as scraper:
        books = scraper._extract_book_list(
            {'book-one': {'human_name': 'Book One'}},
            {},
        )

    assert books[0]['authors'] == []
    assert books[0]['publishers'] == []
    assert books[0]['formats'] == []
    assert books[0]['description'] is None
    assert books[0]['image'] is None
    assert books[0]['detail_image'] is None
