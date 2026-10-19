"""Firefox must receive saved theme + canvas CSS before the first network dependency."""
from pathlib import Path
import re

import pytest

from app.web_pages import page_response

WEB = Path(__file__).parents[1] / 'web'


@pytest.mark.parametrize('page', sorted(p.name for p in WEB.glob('*.html')))
def test_rendered_pages_apply_the_theme_before_waiting_for_assets(page):
    html = page_response(str(WEB / page), version='test').body.decode()
    script = '<script id="page-theme">'
    style = '<style id="page-theme-style">'
    assert script in html and style in html
    # Reuse the shared theme-selection script and canvas bootstrap verbatim.
    assert f'{script}{(WEB / "assets/theme.js").read_text()}</script>' in html
    assert f'{style}{(WEB / "assets/initial-theme.css").read_text()}</style>' in html
    script_at, style_at = html.index(script), html.index(style)
    first_network = min(html.index(token) for token in ['<link rel="stylesheet"', '<script src='] if token in html)
    assert script_at < style_at < first_network < html.index('<body>')
    assert 'src="/static/assets/theme.js?' not in html
    assert 'href="/static/assets/theme.css?' in html
    assert html.count('id="page-theme"') == html.count('id="page-theme-style"') == 1
    assert 'no-store' in page_response(str(WEB / page), version='test').headers['cache-control']


def test_initial_canvas_colors_match_the_full_theme_palette():
    full = (WEB / 'assets/theme.css').read_text()
    initial = (WEB / 'assets/initial-theme.css').read_text()
    colors = re.findall(r'--bg:\s*(#[0-9a-f]+)', full)
    assert len(colors) == 3  # light, explicit dark, system dark
    assert re.findall(r'background:\s*(#[0-9a-f]+)', initial) == colors
    assert re.findall(r'(?:\{|;)\s*color-scheme:\s*(\w+)', initial) == ['light', 'dark', 'dark']
