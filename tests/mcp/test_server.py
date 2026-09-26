from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from fastmcp import Client
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from hbetl_mcp import server
from hbetl_mcp.catalog import BundleCatalog
from spider.config.settings import Settings
from spider.database.models import Base, Bundle


BUNDLE_ID = "bundle-1"
BUNDLE_MACHINE_NAME = "example-book-bundle"
BOOK_MACHINE_NAME = "example-book"


def _catalog(tmp_path: Path) -> BundleCatalog:
    db_path = tmp_path / "catalog.db"
    settings = Settings(db_type="sqlite", db_path=str(db_path))
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Bundle(
                id=BUNDLE_ID,
                machine_name=BUNDLE_MACHINE_NAME,
                tile_name="Example Book Bundle",
                category="bundle",
                product_url="https://example.test/bundle",
                start_date_datetime=datetime(2026, 9, 1),
                end_date_datetime=datetime(2026, 10, 1),
                verification_date=datetime(2026, 9, 20),
                is_active=True,
                book_list=[
                    {
                        "machine_name": BOOK_MACHINE_NAME,
                        "title": "Example Book",
                        "authors": ["Example Author"],
                        "publishers": [{"name": "Example Press", "url": "https://example.test"}],
                        "description": "A book used by the MCP tests.",
                        "formats": ["pdf", "epub"],
                        "tiers": ["bt10"],
                    }
                ],
            )
        )
        session.commit()
    engine.dispose()
    return BundleCatalog(settings)


def test_catalog_exposes_bundle_and_book_metadata(tmp_path: Path, monkeypatch):
    catalog = _catalog(tmp_path)
    monkeypatch.setattr(server, "_catalog", catalog)

    bundles = server.list_bundles(query="Example", limit=10)
    assert bundles[0]["machine_name"] == BUNDLE_MACHINE_NAME
    assert bundles[0]["book_count"] == 1

    bundle = server.get_bundle(BUNDLE_MACHINE_NAME)
    assert bundle["books"][0]["title"] == "Example Book"
    assert bundle["books"][0]["publishers"][0]["name"] == "Example Press"

    matches = server.search_books("example author", limit=10)
    assert matches[0]["machine_name"] == BOOK_MACHINE_NAME
    assert matches[0]["bundle_machine_name"] == BUNDLE_MACHINE_NAME

    book = server.get_book(BOOK_MACHINE_NAME)
    assert book["title"] == "Example Book"

    catalog.engine.dispose()


def test_bundle_identifier_search_respects_inactive_filter(tmp_path: Path):
    catalog = _catalog(tmp_path)
    with catalog.session() as session:
        session.add(
            Bundle(
                id="inactive-bundle",
                machine_name="inactive-book-bundle",
                tile_name="Inactive Book Bundle",
                start_date_datetime=datetime(2026, 8, 1),
                end_date_datetime=datetime(2026, 8, 31),
                verification_date=datetime(2026, 9, 20),
                is_active=False,
                book_list=[
                    {
                        "machine_name": "inactive-book",
                        "title": "Inactive Book",
                    }
                ],
            )
        )
        session.commit()

    assert catalog.search_books("Inactive Book", bundle_identifier="inactive-bundle") == []
    assert catalog.search_books(
        "Inactive Book",
        bundle_identifier="inactive-bundle",
        include_inactive=True,
    )[0].bundle_id == "inactive-bundle"
    catalog.engine.dispose()


def test_fastmcp_tools_and_resources_are_registered(tmp_path: Path, monkeypatch):
    catalog = _catalog(tmp_path)
    monkeypatch.setattr(server, "_catalog", catalog)

    async def exercise_client():
        async with Client(server.mcp) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools} >= {
                "list_bundles",
                "get_bundle",
                "search_books",
                "get_book",
            }
            result = await client.call_tool(
                "search_books",
                {"query": "Example Author", "limit": 1},
            )
            assert result.is_error is False
            resource = await client.read_resource(
                f"hbetl://bundles/{BUNDLE_MACHINE_NAME}/books"
            )
            assert "Example Book" in resource[0].text

    asyncio.run(exercise_client())
    catalog.engine.dispose()
