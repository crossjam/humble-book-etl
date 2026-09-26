from api.main import app


def test_mcp_server_is_mounted_on_api_app():
    assert any(route.path == "/mcp" for route in app.routes)
