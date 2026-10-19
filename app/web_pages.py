"""Shared server-rendered page chrome, using the running release version."""
from html import escape
from pathlib import Path
import re

from fastapi.responses import HTMLResponse


def page_response(path: str, *, version: str) -> HTMLResponse:
    """Apply the initial canvas theme and shared footer without extra network probes."""
    content = Path(path).read_text(encoding="utf-8")
    # Firefox can expose its default white canvas while external theme assets load.
    # Apply the saved choice and canvas color before any network dependency.
    theme = Path('web/assets/theme.js').read_text(encoding='utf-8')
    css = Path('web/assets/initial-theme.css').read_text(encoding='utf-8')
    early_theme = f'<script id="page-theme">{theme}</script><style id="page-theme-style">{css}</style>'
    content = re.sub(r'<script src="/static/assets/theme\.js\?v=\d+"></script>', lambda _: early_theme, content, count=1)
    stylesheet = '<link rel="stylesheet" href="/static/assets/page-footer.css?v=2">'
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
