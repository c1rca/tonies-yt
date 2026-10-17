import httpx
import pytest

from app import auth
from app.config import settings

from tonies_fake import Cloud


@pytest.fixture
def cloud_api():
    from app.tonies_api import ToniesAPI
    cloud = Cloud()
    api = ToniesAPI("synthetic@example.invalid", "synthetic-password", client=httpx.Client(transport=httpx.MockTransport(cloud)), poll_timeout=0.01, poll_interval=0)
    yield cloud, api
    api.close()


@pytest.fixture(autouse=True)
def signed_in(request, monkeypatch):
    """Treat every request as signed in, except in tests marked real_auth."""
    auth.reset_for_tests()
    monkeypatch.setattr(settings, "allowed_hosts", ("localhost", "127.0.0.1", "::1", "testserver", "tonies.local"))
    monkeypatch.setattr(settings, "public_url", "")
    if request.node.get_closest_marker("real_auth"):
        yield
        return
    monkeypatch.setattr(auth, "request_session", lambda _request: "test-session")
    monkeypatch.setattr(auth, "session_valid", lambda token: token == "test-session")
    yield


def pytest_configure(config):
    config.addinivalue_line("markers", "real_auth: run with the real session guard")
