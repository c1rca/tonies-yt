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


def test_assets_revalidate_and_reuse_unchanged_bodies_without_pinning_stale_urls(monkeypatch):
    monkeypatch.setattr(main, "start_worker", lambda: None)
    with TestClient(main.app) as client:
        current = next(attrs['href'] for tag, attrs in _tags()
                       if tag == 'link' and '/theme.css?' in attrs.get('href', ''))
        versioned = client.get(current)
        plain = client.get("/static/assets/theme.css")
        missing = client.get("/static/assets/nope.css?v=1")
        unchanged = client.get(current, headers={'If-None-Match': versioned.headers['etag']})
        for path in ["/static/assets/theme.css?v=garbage", "/static/assets/theme.css?v=2",
                     "/static/assets/theme.css?v=4&v=3", "/static/assets/theme.css?v="]:
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers['cache-control'] == 'no-cache', path
        document = client.get('/static/index.html?v=1')
        assert document.status_code == 200 and document.headers['cache-control'] == 'no-store'
    assert versioned.status_code == 200
    assert versioned.headers["cache-control"] == 'no-cache'
    assert plain.headers["cache-control"] == "no-cache"
    assert unchanged.status_code == 304 and unchanged.content == b''
    assert unchanged.headers['cache-control'] == 'no-cache'
    assert missing.status_code == 404 and "immutable" not in missing.headers.get("cache-control", "")


def test_every_page_asset_url_carries_a_version():
    # Explicit versions distinguish changed assets on navigation and in already-open clients.
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


def test_failed_local_refresh_exits_loading_and_manual_refresh_handles_rejection():
    run_js("""
renderFiles = actualRenderFiles; refreshFiles = actualRefreshFiles;
filesCache = []; api = async () => { throw Error('Offline'); };
await assert.rejects(refreshFiles(), /Offline/);
assert.equal(loadingFiles, false);
assert.doesNotMatch(filesEl.innerHTML, /skeleton/);
const messages = []; showToast = message => messages.push(message);
initLocalFiles();
await document.getElementById('refresh-files').onclick();
assert.match(messages.at(-1), /Could not refresh.*Offline/);
api = async () => [{name:'Back.mp3'}];
await document.getElementById('refresh-files').onclick();
assert.match(filesEl.innerHTML, /Back/);
""")


def test_boot_fail_open_is_bounded_even_when_later_scripts_never_start():
    import json
    gate = (WEB / 'assets/boot-gate.js').read_text()
    run_js("""
const removed = [];
document.documentElement.classList.remove = name => removed.push(name);
""" + "new Function(" + json.dumps(gate) + ")();" + """
const deadline = [...timers.values()].find(t => t.ms === 1500);
assert.ok(deadline); assert.deepEqual(removed, []);
deadline.fn(); assert.deepEqual(removed, ['booting']);
""")


def test_tonie_status_line_keeps_its_space_when_empty():
    css = page_css()
    rule = re.search(r"#tonies-status:empty\s*\{([^}]*)\}", css)
    assert rule and "display:none" not in rule.group(1).replace(" ", "")
    assert re.search(r"#tonies-status\s*\{[^}]*min-height:\s*1\.5em", css)
    # Reserve geometry rather than rehydrating a potentially cross-account capability.
    assert re.search(r"#workspace-undo\[hidden\]\s*\{[^}]*visibility:hidden", css)
    assert re.search(r"#workspace-undo\s*\{[^}]*min-height:53px", css)


TAB = """
const store = new Map();
global.sessionStorage = {getItem:k => store.get(k) ?? null, setItem:(k, v) => store.set(k, String(v)),
  removeItem:k => store.delete(k)};
toniesCache = [{edit_url:'tonie-a', name:'Fox'}];
const later = new Date(Date.now() + 60000).toISOString();
"""


def test_return_never_rehydrates_actionable_undo_from_another_account_or_old_tab_cache():
    run_js(TAB + """
store.set(WORKSPACE_ACTIONS_TAB_KEY, JSON.stringify([{id:'old-account', target_url:'tonie-a',
 type:'rename', status:'done', undo_available:true, undo_expires_at:later}]));
restoreWorkspaceActionsForTab();
assert.equal(workspaceUndoAction('tonie-a'), null);
assert.equal(document.getElementById('workspace-undo-button').disabled, true);
assert.equal(store.has(WORKSPACE_ACTIONS_TAB_KEY), false);
api = async () => ({actions:[{id:'new-account', target_url:'tonie-a', type:'rename',
 status:'done', undo_available:true, undo_expires_at:later}]});
await refreshWorkspaceActions();
assert.equal(workspaceUndoAction('tonie-a')?.id, 'new-account');
assert.equal(document.getElementById('workspace-undo-button').disabled, false);
api = async () => ({actions:[]});
await refreshWorkspaceActions();
assert.equal(workspaceUndoAction('tonie-a'), null);
api = async () => ({actions:[{id:'second', target_url:'tonie-a', type:'rename',
 status:'done', undo_available:true, undo_expires_at:later}]});
await refreshWorkspaceActions();
assert.equal(workspaceUndoAction('tonie-a')?.id, 'second');
api = async () => { throw Error('offline'); };
await refreshWorkspaceActions();
assert.equal(workspaceUndoAction('tonie-a'), null);
assert.equal(document.getElementById('workspace-undo-button').disabled, true);
""")


def test_undo_offers_are_not_persisted_and_lock_forgets_legacy_cache():
    run_js(TAB + """
recordWorkspaceAction('tonie-a', {id:'live', type:'rename', status:'done', undo_available:true, undo_expires_at:later,
 params:{token:'private', title:'Private title'}});
renderWorkspaceUndo();
assert.equal(store.has(WORKSPACE_ACTIONS_TAB_KEY), false);
store.set(WORKSPACE_ACTIONS_TAB_KEY, 'legacy');
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


def test_coming_back_reopens_the_same_add_audio_tab_and_phone_view():
    run_js("""
const local = new Map(), tab = new Map();
global.localStorage = {getItem:k => local.get(k) ?? null, setItem:(k, v) => local.set(k, String(v))};
global.sessionStorage = {getItem:k => tab.get(k) ?? null, setItem:(k, v) => tab.set(k, String(v))};
restoreWorkspacePlace();
assert.equal(workspaceSource, 'youtube'); assert.equal(workspaceView, 'tracks');
setWorkspaceSource('local'); setWorkspaceView('add');
workspaceSource = 'youtube'; workspaceView = 'tracks';  // a new page
restoreWorkspacePlace();
assert.equal(workspaceSource, 'local');
assert.equal(document.getElementById('source-local').hidden, false);
assert.equal(workspaceView, 'add');
// The Add audio tab is remembered across visits; the phone view only for this browser tab.
assert.equal(local.get('tonies-auto.workspace-source.v1'), 'local');
assert.equal(tab.get('tonies-auto.workspace-view.v1'), 'add');
local.set('tonies-auto.workspace-source.v1', 'nonsense'); tab.set('tonies-auto.workspace-view.v1', '<b>');
restoreWorkspacePlace();
assert.equal(workspaceSource, 'youtube'); assert.equal(workspaceView, 'tracks');
""")
    boot = (WEB / "assets/app-boot.js").read_text(encoding="utf-8")
    assert "restoreWorkspacePlace();" in boot and "setWorkspaceSource('youtube')" not in boot
