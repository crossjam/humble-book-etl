from spider.scrapers.bundle_detail_scraper import BundleDetailScraper


def test_extract_book_list_includes_detail_panel_metadata():
    scraper = BundleDetailScraper()
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
    scraper = BundleDetailScraper()

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
