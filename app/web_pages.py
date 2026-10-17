"""Shared server-rendered page chrome, using the running release version."""
from html import escape
from pathlib import Path

from fastapi.responses import HTMLResponse


def page_response(path: str, *, version: str) -> HTMLResponse:
    """Add one consistent footer without JavaScript or a version-network probe."""
    content = Path(path).read_text(encoding="utf-8")
    stylesheet = '<link rel="stylesheet" href="/static/assets/page-footer.css">'
    footer = (
        '<footer class="app-version-footer" aria-label="Application version">'
        '<a href="https://github.com/c1rca/tonies-yt" target="_blank" '
        'rel="noopener noreferrer">Tonies-YT on GitHub</a>'
        f'<span>v{escape(version)}</span></footer>'
    )
    content = content.replace("</head>", f"{stylesheet}\n</head>", 1)
    content = content.replace("</body>", f"{footer}\n</body>", 1)
    return HTMLResponse(content, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    })
