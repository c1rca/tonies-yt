"""All page footers reflect the running application, without extra API calls."""
from html.parser import HTMLParser

from fastapi.testclient import TestClient
import pytest

from app import __version__, main
from app.routes import pages


class FooterReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.versions = []

    def handle_starttag(self, tag, attrs):
        if tag == "footer":
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "footer":
            self.depth -= 1

    def handle_data(self, data):
        if self.depth and data.strip().startswith("v"):
            self.versions.append(data.strip())


@pytest.mark.parametrize("route", ["/", "/settings", "/logs", "/login", "/setup"])
def test_every_page_footer_uses_running_version(monkeypatch, route):
    # Exercise each page handler, not redirects to a different page.
    monkeypatch.setattr(pages, "vault_configured", lambda: route == "/login")
    monkeypatch.setattr(pages, "setup_status", lambda: {"configured": route == "/login", "unlocked": route != "/login"})
    monkeypatch.setattr(main, "start_worker", lambda: None)
    with TestClient(main.app, follow_redirects=False) as client:
        first = client.get(route)
        assert first.status_code == 200
        assert first.headers["content-type"].startswith("text/html")
        assert "no-store" in first.headers["cache-control"]
        parsed = FooterReader()
        parsed.feed(first.text)
        assert parsed.versions == [f"v{__version__}"]
        # Pages use their imported application version, never an HTML pin.
        monkeypatch.setattr(pages, "__version__", "99.8.7")
        changed = client.get(route)
        parsed = FooterReader()
        parsed.feed(changed.text)
        assert parsed.versions == ["v99.8.7"]
        assert "Tonies-YT on GitHub" in changed.text
