"""The page's source as tests see it: markup plus every script and stylesheet, in page order.

Tests check text through these helpers, not by slicing index.html, so moving code between
asset files never breaks a test.
"""
from html.parser import HTMLParser
from pathlib import Path
import re

WEB = Path(__file__).parents[1] / "web"
HTML = WEB / "index.html"
# Not run by the test harness: session.js talks to the network, app-boot.js starts the page
# and boot-gate.js only arms its page-load timer.
BOOT_ASSETS = frozenset({"assets/session.js", "assets/boot-gate.js", "assets/app-boot.js"})


class _Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.styles = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.scripts.append(_asset(attrs["src"]))
        elif tag == "link" and attrs.get("rel") == "stylesheet":
            self.styles.append(_asset(attrs["href"]))


def _asset(url: str) -> str:
    if not url.startswith("/static/assets/"):
        raise AssertionError(f"Unexpected asset origin: {url}")
    return url.split("?", 1)[0].removeprefix("/static/")


def _assets() -> _Assets:
    parser = _Assets()
    parser.feed(HTML.read_text(encoding="utf-8"))
    return parser


def script_paths(*, include_boot: bool = True) -> list[Path]:
    """Script files in page order."""
    return [WEB / s for s in _assets().scripts if include_boot or s not in BOOT_ASSETS]


def page_source() -> str:
    """index.html followed by every script it loads."""
    return "\n".join([HTML.read_text(encoding="utf-8"), *(p.read_text(encoding="utf-8") for p in script_paths())])


def page_css() -> str:
    return "\n".join((WEB / s).read_text(encoding="utf-8") for s in _assets().styles)


_FUNCTION = re.compile(r"^(?:async )?function (\w+)\s*\(", re.M)


def function_source(name: str) -> str:
    """The full text of top-level JS function ``name``, from whichever file defines it."""
    for path in script_paths():
        text = path.read_text(encoding="utf-8")
        starts = [(m.start(), m.group(1)) for m in _FUNCTION.finditer(text)]
        for i, (start, found) in enumerate(starts):
            if found == name:
                end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
                return text[start:end]
    raise AssertionError(f"No top-level function {name} in the page's scripts")
