import httpx

from hbetl_cli.client import ApiClient


def test_inactive_pagination_preserves_snapshot_and_handles_null_end_date():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                headers={"X-Snapshot-At": "2026-09-20T12:00:00Z"},
                json=[{"id": "two", "end_date_datetime": "2026-09-19T00:00:00Z"}],
            )
        if len(requests) == 2:
            return httpx.Response(200, json=[{"id": "one", "end_date_datetime": None}])
        return httpx.Response(200, json=[])

    with ApiClient("https://api.example.test", transport=httpx.MockTransport(handler)) as client:
        result = list(client.iter_inactive_bundles(page_size=1))

    assert [item["id"] for item in result] == ["two", "one"]
    assert requests[1].url.params["snapshot_at"] == "2026-09-20T12:00:00Z"
    assert requests[1].url.params["before_id"] == "two"
    assert "before_end_date" in requests[1].url.params
    assert requests[2].url.params["before_id"] == "one"
    assert "before_end_date" not in requests[2].url.params


def test_client_sends_bearer_token_and_encodes_identifiers():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    with ApiClient("https://api.example.test", "secret", transport=httpx.MockTransport(handler)) as client:
        assert client.get_bundle("name with space", machine_name=True) == {"ok": True}
        assert client.raw_html("bundle/1") == {"ok": True}

    assert seen[0].url.raw_path == b"/bundles/by-machine-name/name%20with%20space"
    assert seen[1].headers["Authorization"] == "Bearer secret"
    assert seen[1].url.raw_path == b"/bundles/bundle%2F1/raw-html"
