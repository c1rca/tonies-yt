"""Enumerate the mounted API and enforce sessions before any handler runs."""
import re
import socket

import pytest
from fastapi.testclient import TestClient

from app import auth, main

pytestmark = pytest.mark.real_auth
PUBLIC = {"/api/health", "/api/setup/status", "/api/setup/login", "/api/setup/init"}


def api_routes():
    return sorted(
        (method, route.path)
        for route in main.app.routes
        if getattr(route, "path", "").startswith("/api/")
        for method in route.methods
    )


def test_public_api_allowlist_matches_registered_routes():
    paths = {path for _, path in api_routes()}
    assert PUBLIC <= paths
    assert {path for path in paths if auth._is_public(path)} == PUBLIC
    assert len(paths - PUBLIC) >= 25


@pytest.mark.parametrize("method,path", [(m, p) for m, p in api_routes() if p not in PUBLIC])
@pytest.mark.parametrize("cookie", [None, "synthetic-invalid-session"])
def test_every_registered_private_api_requires_session(monkeypatch, method, path, cookie):
    def forbidden(*args, **kwargs):
        pytest.fail("A signed-out request must not start workers or connect to a provider")

    monkeypatch.setattr(main, "start_worker", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    client = TestClient(main.app, follow_redirects=False)
    if cookie:
        client.cookies.set(auth.COOKIE_NAME, cookie)
    url = re.sub(r"\{[^}]+\}", "synthetic", path)
    response = client.request(method, url, content=b"not valid JSON", headers={"content-type": "application/json"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Sign in required"}
    assert response.headers["cache-control"] == "no-store"
