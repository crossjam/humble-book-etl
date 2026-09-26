"""FastMCP server exposing the Humble Book ETL catalog."""

from __future__ import annotations

import argparse
import json
from typing import Any

from fastmcp import FastMCP

from .catalog import BundleCatalog

mcp = FastMCP(
    "Humble Book ETL",
    instructions=(
        "Explore Humble Bundle records and the book metadata embedded in each bundle. "
        "Use list_bundles to discover records, get_bundle for bundle metadata and books, "
        "and search_books to find titles, authors, or publishers."
    ),
)
_catalog: BundleCatalog | None = None


def get_catalog() -> BundleCatalog:
    global _catalog
    if _catalog is None:
        _catalog = BundleCatalog()
    return _catalog


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def list_bundles(
    query: str | None = None,
    include_inactive: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List active bundles, optionally searching bundle metadata.

    Args:
        query: Case-insensitive text to match against bundle metadata.
        include_inactive: Include retained inactive bundles when true.
        limit: Maximum number of bundles to return, from 1 through 100.
        offset: Number of matching bundles to skip.
    """
    return [item.model_dump(mode="json") for item in get_catalog().list_bundles(query, include_inactive, limit, offset)]


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def get_bundle(identifier: str, include_books: bool = True) -> dict[str, Any]:
    """Get a bundle by UUID or machine name, including embedded book metadata.

    Args:
        identifier: Bundle UUID or machine name.
        include_books: Include the embedded book metadata in the response.
    """
    return get_catalog().get_bundle(identifier, include_books).model_dump(mode="json")


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def get_featured_bundle() -> dict[str, Any]:
    """Get the active featured bundle selected by the API's bundle ranking."""
    return get_catalog().featured_bundle().model_dump(mode="json")


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def search_books(
    query: str,
    author: str | None = None,
    publisher: str | None = None,
    bundle_identifier: str | None = None,
    include_inactive: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search embedded book metadata and return each match with its containing bundle.

    Args:
        query: Text to match against title, authors, publishers, description, or content type.
        author: Optional case-insensitive author filter.
        publisher: Optional case-insensitive publisher filter.
        bundle_identifier: Restrict the search to one bundle UUID or machine name.
        include_inactive: Include books from retained inactive bundles.
        limit: Maximum number of book matches, from 1 through 100.
    """
    return [
        item.model_dump(mode="json")
        for item in get_catalog().search_books(
            query,
            author=author,
            publisher=publisher,
            bundle_identifier=bundle_identifier,
            include_inactive=include_inactive,
            limit=limit,
        )
    ]


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def get_book(
    identifier: str,
    bundle_identifier: str | None = None,
    include_inactive: bool = False,
) -> dict[str, Any]:
    """Get one book by machine name or exact title, optionally within a bundle.

    Args:
        identifier: Exact book machine name or title.
        bundle_identifier: Optional bundle UUID or machine name to disambiguate matches.
        include_inactive: Include retained inactive bundles when searching.
    """
    return get_catalog().get_book(identifier, bundle_identifier, include_inactive).model_dump(mode="json")


@mcp.resource("hbetl://catalog")
def catalog_resource() -> str:
    """Return a compact catalog of active bundles for orientation."""
    return json.dumps(list_bundles(limit=100), indent=2)


@mcp.resource("hbetl://bundles/{identifier}")
def bundle_resource(identifier: str) -> str:
    """Return bundle metadata and embedded books as JSON."""
    return json.dumps(get_bundle(identifier), indent=2)


@mcp.resource("hbetl://bundles/{identifier}/books")
def bundle_books_resource(identifier: str) -> str:
    """Return only the books embedded in one bundle as JSON."""
    bundle = get_bundle(identifier)
    return json.dumps(bundle.get("books", []), indent=2)


@mcp.resource("hbetl://books/{bundle_identifier}/{book_identifier}")
def book_resource(bundle_identifier: str, book_identifier: str) -> str:
    """Return one book's metadata and its containing bundle as JSON."""
    return json.dumps(get_book(book_identifier, bundle_identifier), indent=2)


def main(argv: list[str] | None = None) -> None:
    """Run the MCP server over stdio or Streamable HTTP."""
    parser = argparse.ArgumentParser(description="Humble Book ETL MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http", "http", "sse"),
        default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
