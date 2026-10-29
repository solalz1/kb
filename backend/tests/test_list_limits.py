"""Out-of-range limits, offsets and periods in a query are clamped, never a server error."""

import pytest
from fastapi.testclient import TestClient

from .test_e2e import AUTH, _mcp


@pytest.fixture
def client(clean_db, fake_llm):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("url", [
    "/api/items?offset=-1",
    "/api/tags?limit=-1",
    "/api/tags?limit=0",
    "/api/tags?limit=99999999999",
    "/api/entities?limit=-5",
    "/api/entities?limit=99999999999",
])
def test_list_endpoints_clamp_their_limits(client, url):
    r = client.get(url, headers=AUTH)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("name,arguments", [
    ("list_recent", {"days": 10 ** 9}),
    ("browse_kb", {"since_days": 10 ** 10}),
])
def test_mcp_periods_are_clamped(client, name, arguments):
    r = _mcp(client, "/mcp/mcp-secret", "tools/call", {"name": name, "arguments": arguments})
    result = r.json()["result"]
    assert not result.get("isError"), result
