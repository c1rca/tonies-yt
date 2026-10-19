"""No flash when coming back to the workspace from Settings, Logs or another page."""
from html.parser import HTMLParser
from pathlib import Path
import re

from fastapi.testclient import TestClient

from app import main
from page_sources import HTML, page_css
from test_frontend_contracts import run_js

WEB = Path(__file__).parents[1] / "web"


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _tags(path=HTML):
    parser = _Tags()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.tags


def test_versioned_assets_are_cached_for_good_and_unversioned_ones_revalidate(monkeypatch):
    monkeypatch.setattr(main, "start_worker", lambda: None)
    with TestClient(main.app) as client:
        versioned = client.get("/static/assets/theme.css?v=2")
        plain = client.get("/static/assets/theme.css")
        missing = client.get("/static/assets/nope.css?v=1")
    assert versioned.status_code == 200
    assert versioned.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert plain.headers["cache-control"] == "no-cache"
    assert missing.status_code == 404 and "immutable" not in missing.headers.get("cache-control", "")


def test_every_page_asset_url_carries_a_version():
    # Versioned assets are cached for a year, so an unversioned URL would never update.
    for page in WEB.glob("*.html"):
        for _tag, attrs in _tags(page):
            url = attrs.get("src") or attrs.get("href") or ""
            if url.startswith("/static/"):
                assert re.search(r"\?v=\d+$", url), f"{page.name}: {url}"
    for asset in [*WEB.glob("assets/*.js"), *WEB.glob("assets/*.css")]:
        for url in re.findall(r"/static/[^'\"`)\s]+", asset.read_text(encoding="utf-8")):
            assert "?v=" in url, f"{asset.name}: {url}"


def test_the_workspace_is_not_shown_until_boot_has_drawn_the_cached_library():
    tags = _tags()
    html = next(attrs for tag, attrs in tags if tag == "html")
    assert "booting" in html.get("class", "").split()
    expect = [attrs for tag, attrs in tags if tag == "link" and attrs.get("rel") == "expect"]
    assert expect == [{"rel": "expect", "href": "#workspace-ready", "blocking": "render"}]
    order = [attrs.get("src", "").split("?")[0] or attrs.get("id") for tag, attrs in tags
             if tag == "script" or attrs.get("id") == "workspace-ready"]
    # The render-blocking target comes right after the last script, so rendering waits for boot.
    assert order[-2:] == ["/static/assets/app-boot.js", "workspace-ready"]
    assert order[0] == "/static/assets/theme.js" and order[1] == "/static/assets/boot-gate.js"
    boot = (WEB / "assets/app-boot.js").read_text(encoding="utf-8")
    reveal = boot.index("classList.remove('booting')")
    assert boot.index("renderToniesContent();") < reveal < boot.index("void refreshTonies(")
    assert ".booting .workspace" in page_css()
    gate = (WEB / "assets/boot-gate.js").read_text(encoding="utf-8")
    assert "classList.remove('booting')" in gate and "setTimeout" in gate


def test_cleared_results_show_their_empty_state_while_jobs_load():
    run_js("""
resultsCleared = true; loadingJobs = true; jobsCache = [];
assert.match(getResultsEmptyStateHtml({isEmpty:true}), /No results yet/);
resultsCleared = false;
assert.match(getResultsEmptyStateHtml({isEmpty:true}), /skeleton/);
""")


def test_tonie_status_line_keeps_its_space_when_empty():
    css = page_css()
    rule = re.search(r"#tonies-status:empty\s*\{([^}]*)\}", css)
    assert rule and "display:none" not in rule.group(1).replace(" ", "")
    assert re.search(r"#tonies-status\s*\{[^}]*min-height:\s*1\.5em", css)


TAB = """
const store = new Map();
global.sessionStorage = {getItem:k => store.get(k) ?? null, setItem:(k, v) => store.set(k, String(v)),
  removeItem:k => store.delete(k)};
toniesCache = [{edit_url:'tonie-a', name:'Fox'}];
const later = new Date(Date.now() + 60000).toISOString();
"""


def test_undo_bar_is_drawn_at_once_from_this_tabs_last_answer_then_rechecked():
    run_js(TAB + """
recordWorkspaceAction('tonie-a', {id:'a1', type:'rename', status:'done', undo_available:true, undo_expires_at:later});
renderWorkspaceUndo();
// A fresh page in the same tab: nothing from the server yet.
workspaceActionHistory.clear();
assert.equal(workspaceUndoAction('tonie-a'), null);
restoreWorkspaceActionsForTab();
assert.equal(workspaceUndoAction('tonie-a')?.id, 'a1');
assert.equal(document.getElementById('workspace-undo').hidden, false);
// The server no longer offers it (restart, Lock elsewhere): the bar goes.
api = async () => ({actions: []});
await refreshWorkspaceActions();
assert.equal(workspaceUndoAction('tonie-a'), null);
assert.equal(document.getElementById('workspace-undo').hidden, true);
""")


def test_tab_cache_keeps_only_live_undo_offers_and_lock_forgets_it():
    run_js(TAB + """
const past = new Date(Date.now() - 1000).toISOString();
recordWorkspaceAction('tonie-a', {id:'old', type:'move', status:'done', undo_available:true, undo_expires_at:past});
recordWorkspaceAction('tonie-a', {id:'gone', type:'move', status:'done', undo_available:false});
recordWorkspaceAction('tonie-a', {id:'live', type:'rename', status:'done', undo_available:true, undo_expires_at:later});
renderWorkspaceUndo();
const saved = JSON.parse(store.get(WORKSPACE_ACTIONS_TAB_KEY));
assert.deepEqual(saved.map(a => a.id), ['live']);
assert.deepEqual(Object.keys(saved[0]).sort(),
  ['id', 'status', 'target_url', 'type', 'undo_available', 'undo_expires_at', 'undo_pending']);
window.location = {};
api = async () => ({});
await lockApp();
assert.equal(store.has(WORKSPACE_ACTIONS_TAB_KEY), false);
""")


def test_cross_page_navigation_cross_fades_with_a_steady_header():
    css = (WEB / "assets/theme.css").read_text(encoding="utf-8")
    motion = css[css.index("@media(prefers-reduced-motion:no-preference)"):]
    assert "@view-transition" in motion and "navigation:auto" in motion.replace(" ", "")
    assert "view-transition-name:app-header" in css.replace(" ", "")



def test_tonie_art_and_rail_are_not_redrawn_when_nothing_changed():
    # Redrawing recreates the <img> elements, which blink while they decode.
    run_js("""
const counted = id => { const el = document.getElementById(id); let html = '', writes = 0;
  Object.defineProperty(el, 'innerHTML', {get: () => html, set: v => { html = v; writes++; }});
  return () => writes; };
const art = counted('selected-tonie-art'), rail = counted('tonies');
toniesCache = [{edit_url:'tonie-a', name:'Fox', image:'https://img.example/fox.png'},
               {edit_url:'tonie-b', name:'Owl', image:'https://img.example/owl.png'}];
selectedToniesUrl = 'tonie-a';
actualRenderTonies(); actualRenderTonies(); setToniesStatus('Loading Tonies library…', true);
assert.equal(art(), 1); assert.equal(rail(), 1);
selectedToniesUrl = 'tonie-b'; actualRenderTonies();
assert.equal(art(), 2); assert.equal(rail(), 2);
toniesCache = []; loadingTonies = true; actualRenderTonies();
assert.match(document.getElementById('tonies').innerHTML, /skeleton/);
toniesCache = [{edit_url:'tonie-b', name:'Owl', image:'https://img.example/owl.png'}]; loadingTonies = false;
actualRenderTonies();
assert.match(document.getElementById('tonies').innerHTML, /Owl/);
""")
