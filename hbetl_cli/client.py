"""HTTP client for the Humble Book ETL API."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx


class ApiError(RuntimeError):
    """An API request failed with a useful, user-facing error."""

    def __init__(self, status_code: int, message: str, method: str, path: str):
        super().__init__(message)
        self.status_code = status_code
        self.method = method
        self.path = path
        self.message = message


def _cursor_timestamp(value: str | None) -> str | None:
    """Return an offset-aware UTC timestamp accepted by the archive API."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


class ApiClient:
    def __init__(self, base_url: str, token: str | None = None, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = httpx.Client(base_url=self.base_url, transport=transport, timeout=60.0)

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, *, auth: bool = False, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        if auth:
            if not self.token:
                raise ApiError(401, "Authentication required; run `hbetl login` first.", method, path)
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self.http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(0, f"Could not reach API at {self.base_url}: {exc}", method, path) from exc
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except (ValueError, TypeError):
                detail = response.text
            if isinstance(detail, list):
                detail = "; ".join(str(item.get("msg", item)) if isinstance(item, dict) else str(item) for item in detail)
            message = str(detail).strip() or response.reason_phrase
            if response.status_code == 401:
                message += " (run `hbetl login` to authenticate)"
            raise ApiError(response.status_code, message, method, path)
        return response

    def request_json(self, method: str, path: str, *, auth: bool = False, **kwargs: Any) -> Any:
        return self._request(method, path, auth=auth, **kwargs).json()

    def health(self) -> dict[str, Any]:
        return self.request_json("GET", "/health")

    def login(self, username: str, password: str) -> dict[str, Any]:
        return self.request_json("POST", "/auth/login", json={"username": username, "password": password})

    def me(self) -> dict[str, Any]:
        return self.request_json("GET", "/auth/me", auth=True)

    def list_bundles(self, *, include_inactive: bool = False, limit: int | None = None, offset: int = 0,
                     snapshot_at: str | None = None, before_end_date: str | None = None,
                     before_id: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {"include_inactive": include_inactive, "offset": offset}
        if limit is not None:
            params["limit"] = limit
        if snapshot_at:
            params["snapshot_at"] = snapshot_at
        if before_end_date:
            params["before_end_date"] = before_end_date
        if before_id:
            params["before_id"] = before_id
        response = self._request("GET", "/bundles", params=params)
        return response.json(), response.headers.get("X-Snapshot-At")

    def iter_inactive_bundles(self, *, page_size: int = 100, max_items: int | None = 10_000) -> Iterator[dict[str, Any]]:
        snapshot_at: str | None = None
        before_end_date: str | None = None
        before_id: str | None = None
        yielded = 0
        while max_items is None or yielded < max_items:
            request_limit = page_size if max_items is None else min(page_size, max_items - yielded)
            page, response_snapshot = self.list_bundles(
                include_inactive=True,
                limit=request_limit,
                snapshot_at=snapshot_at,
                before_end_date=before_end_date,
                before_id=before_id,
            )
            snapshot_at = snapshot_at or response_snapshot
            if not page:
                return
            for bundle in page:
                yield bundle
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            last = page[-1]
            before_id = last["id"]
            before_end_date = _cursor_timestamp(last.get("end_date_datetime"))

    def get_bundle(self, identifier: str, *, machine_name: bool = False) -> dict[str, Any]:
        path = f"/bundles/by-machine-name/{quote(identifier, safe='')}" if machine_name else f"/bundles/{quote(identifier, safe='')}"
        return self.request_json("GET", path)

    def featured_bundle(self) -> dict[str, Any]:
        return self.request_json("GET", "/bundles/featured")

    def raw_html(self, bundle_id: str) -> dict[str, Any]:
        return self.request_json("GET", f"/bundles/{quote(bundle_id, safe='')}/raw-html", auth=True)

    def raw_data(self, raw_data_id: str | None = None, *, latest: bool = False) -> Any:
        if latest:
            path = "/landing-page-raw-data/latest"
        elif raw_data_id:
            path = f"/landing-page-raw-data/{quote(raw_data_id, safe='')}"
        else:
            path = "/landing-page-raw-data"
        return self.request_json("GET", path)

    def run_etl(self) -> dict[str, Any]:
        return self.request_json("POST", "/etl/run", auth=True)

    def lifecycle_events(self, *, machine_name: str | None = None, event_type: str | None = None,
                         limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if machine_name:
            params["machine_name"] = machine_name
        if event_type:
            params["event_type"] = event_type
        return self.request_json("GET", "/bundle-lifecycle-events", params=params)
