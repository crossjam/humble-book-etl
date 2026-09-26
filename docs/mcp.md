# Humble Book ETL MCP server

The repository includes a read-only [FastMCP](https://gofastmcp.com/getting-started/welcome)
server for exploring the bundle catalog and the book metadata embedded in each
`Bundle.book_list` record.

## Install and run

Install the project, which includes the MCP dependency:

```bash
python -m pip install -e .
```

Point the server at the same database as the API. SQLite is the default:

```bash
export DB_DB_PATH=/path/to/humble-book-etl/data/humble_bundle.db
hbetl-mcp
```

The default transport is stdio, which is the recommended mode for a local MCP
client. For local Streamable HTTP testing, bind only to loopback:

```bash
hbetl-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

## API route

The FastAPI application mounts the same MCP server at `/mcp`. When the API is
running on its normal port, use:

```text
http://localhost:5002/mcp
```

This route runs in the API process, shares its database configuration and
lifespan, and does not require a second MCP systemd service. The standalone
`hbetl-mcp` command remains useful for stdio clients or an independently
managed MCP listener.

The server is intentionally read-only and does not expose credentials, raw
HTML, login, or ETL mutation operations. Do not publish the HTTP listener
without adding deployment authentication and network controls.

## Tools

- `list_bundles`: discover active bundles by title, machine name, category, or author; optionally include inactive records.
- `get_bundle`: fetch bundle metadata and its embedded book records by UUID or machine name.
- `get_featured_bundle`: fetch the active bundle selected by the API's featured-bundle ordering.
- `search_books`: search title, machine name, description, authors, publishers, and content type; results include containing-bundle metadata.
- `get_book`: fetch a book by exact machine name or title. Supply `bundle_identifier` when the identifier is ambiguous.

Book metadata follows the current scraper shape: title, machine name, authors,
publishers, description, MSRP, preview links, image links, content type,
formats, and pricing tiers. There is no first-class `Book` SQL table or
explicit ISBN field in the current data model; ISBN-like information may only
appear in source publisher URLs or descriptions.

## Resources

- `hbetl://catalog`: compact active-bundle catalog.
- `hbetl://bundles/{identifier}`: bundle metadata plus books.
- `hbetl://bundles/{identifier}/books`: books embedded in one bundle.
- `hbetl://books/{bundle_identifier}/{book_identifier}`: one book with containing-bundle context.

The resource and tool layer reads the existing SQLAlchemy `Bundle` model rather
than maintaining a second catalog or copying data into MCP-specific tables.
