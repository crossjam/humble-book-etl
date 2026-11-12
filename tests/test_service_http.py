"""Exercise the API and mounted MCP service through a real localhost server."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import select
import subprocess
import sys

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.security import hash_password
from spider.database.models import Base, Bundle, User


ACTIVE_IDS = {"current", "featured"}
RETAINED_IDS = ACTIVE_IDS | {"expired", "future", "archived"}
TEST_PASSWORD = "http-regression-test-only"
BOOK_METADATA = {
    "authors": ["Transport Author"],
    "publishers": [{"name": "Transport Press", "url": "https://example.test/press"}],
    "description": "Retained metadata for HTTP regression coverage.",
    "formats": ["pdf", "epub"],
    "tiers": ["bt10"],
    "preview": {"url": "https://example.test/preview"},
}

# Report readiness only after uvicorn has completed both application lifespans
# and bound its ephemeral port. No polling, reserved-port race, or external DB.
SERVER_SCRIPT = """
import sys

def localhost_only(event, args):
    if event == "socket.getaddrinfo":
        host = args[0]
    elif event == "socket.connect" and isinstance(args[1], tuple):
        host = args[1][0]
    else:
        return
    if host not in (None, "localhost", "127.0.0.1", "::1"):
        raise RuntimeError(f"External network access is forbidden in HTTP tests: {host}")

sys.addaudithook(localhost_only)

import uvicorn

class ReadyServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        port = self.servers[0].sockets[0].getsockname()[1]
        print(f"HTTP_READY {port}", flush=True)

ReadyServer(uvicorn.Config(
    "api.main:app",
    host="127.0.0.1",
    port=0,
    lifespan="on",
    log_level="warning",
    access_log=False,
)).run()
"""


@pytest.fixture(scope="module")
def service_http(tmp_path_factory):
    directory = tmp_path_factory.mktemp("service-http")
    db_path = directory / "catalog.db"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    schedules = {
        "current": (-2, 2, True, 20),
        "featured": (-1, 1, True, 100),
        # Inactive candidates deliberately outrank the active featured bundle.
        "expired": (-3, -1, True, 10_000),
        "future": (1, 3, True, 20_000),
        "archived": (-4, -2, False, 30_000),
    }
    try:
        with Session(engine) as session:
            session.add(User(
                id="http-test-user",
                username="http-test",
                email="http-test@example.com",
                password_hash=hash_password(TEST_PASSWORD),
            ))
            for identifier, (start, end, active, msrp) in schedules.items():
                session.add(Bundle(
                    id=identifier,
                    machine_name=f"{identifier}-bundle",
                    tile_name=f"Transport {identifier.title()} Bundle",
                    category="books",
                    product_url=f"https://example.test/{identifier}",
                    start_date_datetime=now + timedelta(days=start),
                    end_date_datetime=now + timedelta(days=end),
                    verification_date=now - timedelta(hours=1),
                    is_active=active,
                    archived_at=now - timedelta(days=1) if identifier == "archived" else None,
                    msrp_total=msrp,
                    price_tiers=[{"name": "bt10", "price": 10}],
                    book_list=[{
                        **BOOK_METADATA,
                        "machine_name": f"{identifier}-book",
                        "title": f"Transport Book {identifier.title()}",
                    }],
                    raw_html=f"<html>Retained {identifier}</html>",
                ))
            session.commit()
    finally:
        engine.dispose()

    # Do not inherit production DB/admin settings or read the repository .env.
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("DB_")
    }
    environment.update({
        "DB_DB_TYPE": "sqlite",
        "DB_DB_PATH": str(db_path),
        "DB_SQL_ECHO": "false",
        "DB_JWT_SECRET_KEY": "http-regression-only-not-a-production-secret",
        "DB_ADMIN_USERNAME": "",
        "DB_ADMIN_EMAIL": "",
        "DB_ADMIN_PASSWORD_PLAIN": "",
        "DB_ADMIN_PASSWORD_SHA": "",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    })
    log_path = directory / "uvicorn.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", SERVER_SCRIPT],
            cwd=directory,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=log,
        )
        try:
            assert process.stdout is not None
            ready, _, _ = select.select([process.stdout], [], [], 30)
            assert ready, f"HTTP server startup timed out:\n{log_path.read_text()}"
            line = process.stdout.readline().decode().strip()
            assert line.startswith("HTTP_READY "), (
                f"HTTP server failed to start ({line!r}):\n{log_path.read_text()}"
            )
            yield f"http://127.0.0.1:{int(line.split()[1])}", db_path
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()


def test_api_and_mcp_over_http(service_http, monkeypatch):
    base_url, db_path = service_http
    # MCP's own HTTP client must bypass any developer/CI proxy for localhost.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    async def exercise():
        async with httpx.AsyncClient(
            base_url=base_url, timeout=10, trust_env=False,
        ) as api:
            async def get(path):
                response = await api.get(path)
                assert response.status_code == 200, response.text
                return response.json()

            health = await get("/health")
            assert health == {"status": "ok", "database": str(db_path)}
            active = await get("/bundles")
            assert {bundle["id"] for bundle in active} == ACTIVE_IDS
            featured = await get("/bundles/featured")
            assert featured["id"] == "featured"

            archive_response = await api.get("/bundles?include_inactive=true")
            assert archive_response.status_code == 200, archive_response.text
            assert archive_response.headers["X-Snapshot-At"]
            assert {bundle["id"] for bundle in archive_response.json()} == RETAINED_IDS

            details = {}
            for identifier in RETAINED_IDS:
                detail = await get(f"/bundles/{identifier}")
                assert detail == await get(
                    f"/bundles/by-machine-name/{identifier}-bundle"
                )
                assert detail["id"] == identifier
                assert detail["book_list"][0]["authors"] == BOOK_METADATA["authors"]
                assert detail["price_tiers"] == [{"name": "bt10", "price": 10}]
                assert "raw_html" not in detail
                details[identifier] = detail
            assert details["expired"]["is_active"] is True
            assert details["archived"]["archived_at"] is not None

            history = await get("/bundle-history")
            assert {item["id"] for item in history} == {"expired", "archived"}
            assert all(item["record_type"] == "inactive_bundle" for item in history)
            assert {item["bundle"]["id"] for item in history} == {"expired", "archived"}

            for method, path in (
                ("POST", "/etl/run"),
                ("GET", "/bundles/current/raw-html"),
                ("GET", "/auth/me"),
            ):
                response = await api.request(method, path)
                assert response.status_code == 401, response.text
                assert response.headers["WWW-Authenticate"] == "Bearer"
            invalid_login = await api.post(
                "/auth/login", json={"username": "http-test", "password": "wrong"},
            )
            assert invalid_login.status_code == 401
            login = await api.post(
                "/auth/login",
                json={"username": "http-test", "password": TEST_PASSWORD},
            )
            assert login.status_code == 200, login.text
            token = login.json()
            assert token["token_type"] == "bearer"
            assert token["user"]["id"] == "http-test-user"
            headers = {"Authorization": f"Bearer {token['access_token']}"}
            me = await api.get("/auth/me", headers=headers)
            assert me.status_code == 200, me.text
            assert me.json() == token["user"]
            raw = await api.get("/bundles/archived/raw-html", headers=headers)
            assert raw.status_code == 200, raw.text
            assert raw.json() == {
                "id": "archived",
                "machine_name": "archived-bundle",
                "raw_html": "<html>Retained archived</html>",
            }
            for path in (
                "/not-a-service-route",
                "/bundles/missing",
                "/bundles/by-machine-name/missing",
            ):
                assert (await api.get(path)).status_code == 404

        # A URL transport (not Client(server.mcp)) exercises the mounted HTTP
        # endpoint, initialization handshake, session, and lifespan management.
        transport = StreamableHttpTransport(f"{base_url}/mcp/")
        async with Client(
            transport, mode="legacy", auto_initialize=False,
            timeout=10, init_timeout=10,
        ) as mcp:
            initialized = await mcp.initialize()
            assert initialized.server_info.name == "Humble Book ETL"
            assert initialized.capabilities.tools is not None
            assert initialized.capabilities.resources is not None
            assert {tool.name for tool in await mcp.list_tools()} == {
                "list_bundles", "get_bundle", "get_featured_bundle",
                "search_books", "get_book",
            }

            async def call(name, arguments=None):
                result = await mcp.call_tool(name, arguments or {})
                assert result.is_error is False
                return result.data

            bundles = await call("list_bundles")
            assert {bundle["id"] for bundle in bundles} == {
                bundle["id"] for bundle in active
            }
            retained = await call("list_bundles", {"include_inactive": True})
            assert {bundle["id"] for bundle in retained} == RETAINED_IDS
            assert {bundle["id"] for bundle in retained if bundle["is_active"]} == ACTIVE_IDS
            assert (await call("get_featured_bundle"))["id"] == featured["id"]

            for identifier in RETAINED_IDS:
                bundle = await call("get_bundle", {"identifier": identifier})
                assert bundle["id"] == details[identifier]["id"]
                assert bundle["is_active"] is (identifier in ACTIVE_IDS)
                for field, value in details[identifier]["book_list"][0].items():
                    assert bundle["books"][0][field] == value
            expired = await call("get_bundle", {"identifier": "expired-bundle"})
            assert expired["id"] == "expired"

            matches = await call("search_books", {"query": "Transport Author"})
            assert {book["bundle_id"] for book in matches} == ACTIVE_IDS
            book = await call("get_book", {"identifier": "current-book"})
            assert book["bundle_id"] == details["current"]["id"]
            assert book["publishers"] == BOOK_METADATA["publishers"]
            for identifier in ("expired", "expired-bundle"):
                arguments = {"query": "Transport", "bundle_identifier": identifier}
                assert await call("search_books", arguments) == []
                retained_books = await call(
                    "search_books", arguments | {"include_inactive": True},
                )
                assert [item["bundle_id"] for item in retained_books] == ["expired"]
                assert retained_books[0]["bundle_is_active"] is False
            unavailable = await mcp.call_tool(
                "get_book", {"identifier": "expired-book"}, raise_on_error=False,
            )
            assert unavailable.is_error is True
            retained_book = await call(
                "get_book", {"identifier": "expired-book", "include_inactive": True},
            )
            assert retained_book["bundle_id"] == "expired"
            assert retained_book["bundle_is_active"] is False

            assert {str(resource.uri) for resource in await mcp.list_resources()} == {
                "hbetl://catalog",
            }
            assert {
                template.uri_template for template in await mcp.list_resource_templates()
            } == {
                "hbetl://bundles/{identifier}",
                "hbetl://bundles/{identifier}/books",
                "hbetl://books/{bundle_identifier}/{book_identifier}",
            }

            async def read(uri):
                contents = await mcp.read_resource(uri)
                assert len(contents) == 1
                return json.loads(contents[0].text)

            catalog = await read("hbetl://catalog")
            assert {bundle["id"] for bundle in catalog} == ACTIVE_IDS
            resource_bundle = await read("hbetl://bundles/expired-bundle")
            assert resource_bundle["id"] == "expired"
            assert resource_bundle["books"][0]["authors"] == BOOK_METADATA["authors"]
            books = await read("hbetl://bundles/current-bundle/books")
            assert books[0]["machine_name"] == "current-book"
            resource_book = await read("hbetl://books/current-bundle/current-book")
            assert resource_book == book

    asyncio.run(exercise())
