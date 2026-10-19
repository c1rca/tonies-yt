"""Area-owned page state and explicitly initialized page wiring contracts."""
from pathlib import Path
import re

import pytest

import test_frontend_contracts as frontend
from page_sources import function_source

ASSETS = Path(__file__).parents[1] / "web" / "assets"


def test_no_area_registers_listeners_before_explicit_initialization():
    frontend.run_js("assert.deepEqual(listenerRegistrations, []);")


def test_navigation_initializer_preserves_keyboard_tabs_and_escape():
    frontend.run_js("""
const documentListeners = new Map();
document.addEventListener = (type, fn) => documentListeners.set(type, fn);
initWorkspaceNavigation();
let prevented = 0, selected;
setWorkspaceSource = (name, focus) => { selected = [name, focus]; };
document.getElementById('source-tab-youtube').dispatchEvent({type:'keydown', key:'ArrowRight', preventDefault(){prevented++;}});
assert.deepEqual(selected, ['local', true]); assert.equal(prevented, 1);
document.getElementById('source-tab-local').dispatchEvent({type:'keydown', key:'Home', preventDefault(){}});
assert.deepEqual(selected, ['youtube', true]);
const closed = [];
toggleTonieSelector = flag => closed.push(flag);
closeToniesMenu = () => closed.push('tonies'); closeFileMenu = () => closed.push('files');
documentListeners.get('keydown')({key:'Escape'});
assert.deepEqual(closed, [false, 'tonies', 'files']);
""")


def test_shared_state_declarations_have_multiple_feature_consumers():
    shared = ASSETS / "app-state.js"
    for name in re.findall(r"^(?:let|const) (\w+)", shared.read_text(), re.M):
        consumers = [path.name for path in ASSETS.glob('*.js')
                     if path != shared and re.search(rf"\b{name}\b", path.read_text())]
        assert len(consumers) >= 2, (name, consumers)


@pytest.fixture(autouse=True)
def declarations_only(monkeypatch):
    """Load the area scripts without initializing any of them."""
    monkeypatch.setattr(frontend, "AREA_INIT", "")


def test_area_scripts_have_no_implicit_wiring_or_window_exports():
    frontend.run_js("""
assert.equal(document.getElementById('refresh-tonies').onclick, undefined);
assert.equal(document.getElementById('search-btn').onclick, undefined);
assert.equal(document.getElementById('refresh-files').onclick, undefined);
assert.equal(window.uploadExisting, undefined);
assert.equal(window.deleteFile, undefined);
assert.equal(window.pickCandidate, undefined);
assert.equal(window.selectTonies, undefined);
assert.equal(window.enqueueToniesDelete, undefined);
filesCache = [{name:'a.mp3'}];
await toggleLocalPlayback('a.mp3');
document.getElementById('local-player-close').dispatchEvent({type:'click'});
assert.equal(localAudioEl.paused, false); // No listener until the area is initialized.
""")


def test_search_initialization_restores_storage_key_and_binds_enter_and_buttons():
    frontend.run_js("""
const keys = [], classes = [];
localStorage.getItem = key => { keys.push(key); return '1'; };
document.documentElement.classList.add = name => classes.push(name);
let searches = 0, clears = 0;
sendQuery = () => { searches++; };
clearResults = () => { clears++; };
initSearch();
assert.deepEqual(keys, [RESULTS_CLEARED_KEY]);
assert.deepEqual(classes, ['hide-search-results']);
searchBtnEl.onclick(); clearResultsEl.onclick();
queryInputEl.dispatchEvent({type:'keydown', key:'Escape'});
queryInputEl.dispatchEvent({type:'keydown', key:'Enter'});
assert.equal(searches, 2); assert.equal(clears, 1);
""")


def test_search_storage_failure_does_not_prevent_wiring():
    frontend.run_js("""
localStorage.getItem = () => { throw Error('Storage unavailable'); };
initSearch();
assert.equal(searchBtnEl.onclick, sendQuery);
assert.equal(clearResultsEl.onclick, clearResults);
""")


def test_local_files_initialization_binds_refresh_and_filter_controls():
    frontend.run_js("""
let renders = 0;
renderFiles = () => { renders++; };
initLocalFiles();
assert.equal(document.getElementById('refresh-files').onclick, refreshFiles);
fileFilterEl.value = 'ducks';
fileFilterEl.oninput();
document.getElementById('clear-filter').onclick();
assert.equal(fileFilterEl.value, ''); assert.equal(renders, 2);
""")


def test_local_preview_initialization_binds_native_audio_errors_and_close():
    frontend.run_js("""
initLocalPreview();
let updates = 0;
filesEl.querySelectorAll = () => { updates++; return []; };
filesCache = [{name:'a.mp3'}];
await toggleLocalPlayback('a.mp3');
const before = updates;
for (const type of ['play', 'pause', 'ended']) localAudioEl.dispatchEvent({type});
assert.equal(updates, before + 3);
localAudioEl.dispatchEvent({type:'error'});
assert.match(localPlayerStatusEl.textContent, /Could not play this file/);
document.getElementById('local-player-close').dispatchEvent({type:'click'});
assert.equal(localAudioEl.paused, true); assert.equal(localAudioEl.src, '');
assert.equal(localPlayerEl.hidden, true);
""")


def test_picker_initialization_preserves_single_flight_refresh_and_recovery():
    frontend.run_js("""
initToniesPicker();
let calls = 0, reject;
refreshTonies = options => {
  assert.deepEqual(options, {force:true}); calls++;
  return new Promise((resolve, fail) => { reject = fail; });
};
const button = document.getElementById('refresh-tonies');
const first = button.onclick();
assert.equal(toniesApplying, true); assert.equal(manualToniesRefreshInFlight, true);
await button.onclick(); assert.equal(calls, 1);
reject(Error('Account needs sign-in')); await first;
assert.equal(toniesApplying, false); assert.equal(manualToniesRefreshInFlight, false);
assert.match(toniesStatusEl.innerHTML, /Account needs sign-in/);
""")


def test_library_initialization_preserves_view_toggle_edit_guard_and_drop_cleanup():
    frontend.run_js("""
const listeners = new Map();
document.addEventListener = (event, fn) => listeners.set(event, fn);
initTonieLibrary();
const removed = [];
toniesContentEl.classList.remove = name => removed.push(name);
for (const event of ['dragend', 'drop']) listeners.get(event)();
assert.deepEqual(removed, ['external-drop', 'external-drop']);
assert.equal(toniesReverseView, true); toggleToniesOrderEl.onclick();
assert.equal(toniesReverseView, false);
toniesContentCache = {chapters:[{chapter_id:'a', title:'Song'}]};
toniesApplying = true; toggleToniesEditEl.onclick(); assert.equal(toniesEditMode, false);
toniesApplying = false; toggleToniesEditEl.onclick(); assert.equal(toniesEditMode, true);
toggleToniesEditEl.onclick(); assert.equal(toniesEditMode, false);
assert.equal(saveToniesNamesEl.onclick, saveToniesNameEdits);
assert.equal(cancelToniesNamesEl.onclick, cancelToniesEditMode);
assert.equal(deleteAllToniesContentEl.onclick, deleteAllToniesContent);
""")


def test_menu_initialization_captures_cloud_identity_and_preserves_keyboard_navigation():
    frontend.run_js("""
initContextMenus();
toniesContentCache = {chapters:[{chapter_id:'a'}, {chapter_id:'b'}]};
let renamed, deleted, movement;
beginInlineRename = id => { renamed = id; };
enqueueToniesDelete = index => { deleted = index; };
moveToniesFromMenu = kind => { movement = kind; };
openToniesMenu(1, 100, 100);
document.getElementById('tonies-menu-rename').onclick();
assert.equal(renamed, 'b'); assert.equal(toniesMenuChapterId, null);
openToniesMenu(1, 100, 100);
document.getElementById('tonies-menu-delete').onclick(); assert.equal(deleted, 1);
toniesMenuUpEl.onclick(); assert.equal(movement, 'up');
const items = [element(), element(), element()];
toniesMenuEl.querySelectorAll = () => items;
items[0].focus();
let prevented = 0;
toniesMenuEl.dispatchEvent({type:'keydown', key:'ArrowDown', preventDefault(){prevented++;}});
assert.equal(document.activeElement, items[1]); assert.equal(prevented, 1);
toniesMenuEl.dispatchEvent({type:'keydown', key:'End', preventDefault(){}});
assert.equal(document.activeElement, items[2]);
openToniesMenu(0, 100, 100);
toniesMenuEl.dispatchEvent({type:'keydown', key:'Tab'});
assert.equal(toniesMenuChapterId, null);
""")


def test_file_menu_initialization_preserves_upload_edges_download_and_outside_click():
    frontend.run_js("""
let outside;
document.addEventListener = (event, fn) => { if (event === 'click') outside = fn; };
initContextMenus();
const kinds = [];
uploadFromFileMenu = kind => kinds.push(kind);
fileMenuUploadEl.onclick(); fileMenuUploadTopEl.onclick(); fileMenuUploadBottomEl.onclick();
assert.deepEqual(kinds, ['default', 'top', 'bottom']);
let downloaded;
downloadLocalFile = name => { downloaded = name; };
openFileMenu('Ducks.mp3', 20, 20); fileMenuDownloadEl.onclick();
assert.equal(downloaded, 'Ducks.mp3');
fileMenuEl.classList.contains = () => true;
fileMenuEl.contains = () => false;
outside({target:element()}); assert.equal(fileMenuFilename, '');
""")


def test_core_initialization_preserves_lock_and_tap_feedback():
    frontend.run_js("""
let pointerdown;
document.addEventListener = (event, fn) => { if (event === 'pointerdown') pointerdown = fn; };
initAppCore();
assert.equal(lockAppEl.onclick, lockApp);
const classes = [], button = element();
button.classList.remove = name => classes.push(['remove',name]);
button.classList.add = name => classes.push(['add',name]);
pointerdown({target:{closest:() => button}});
assert.deepEqual(classes, [['remove','tap-feedback'],['add','tap-feedback']]);
[...timers.values()].find(t => t.ms === 180).fn();
assert.deepEqual(classes.at(-1), ['remove','tap-feedback']);
""")


def test_download_helper_retains_encoding_and_ephemeral_anchor():
    frontend.run_js("""
const anchor = element(), steps = [];
document.createElement = tag => { assert.equal(tag, 'a'); return anchor; };
document.body = {appendChild:a => { assert.equal(a, anchor); steps.push('append'); }};
anchor.click = () => steps.push('click'); anchor.remove = () => steps.push('remove');
fileMenuFilename = 'test';
downloadLocalFile(' Ducks & snow #?.mp3 ');
assert.equal(anchor.download, 'Ducks & snow #?.mp3');
assert.equal(anchor.href, '/api/files/' + encodeURIComponent(anchor.download));
assert.deepEqual(steps, ['append','click','remove']);
assert.equal(fileMenuFilename, '');
""")


def test_state_and_helpers_live_with_their_owning_area():
    shared = (ASSETS / "app-state.js").read_text()
    declarations = re.findall(r"^(?:let|const) (\w+)", shared, re.M)
    caches = {'filesCache', 'jobsCache', 'toniesCache', 'selectedToniesUrl',
              'toniesImageCache', 'toniesContentCache', 'toniesContentByUrlCache'}
    assert all(name.endswith('El') or name in caches or name in {'stCount', 'stSize', 'stLength'}
               for name in declarations)
    owners = {
        'local-preview.js': ['localPlaybackFilename', 'localPlaybackIntent'],
        'search-results.js': ['activeSearchRequestSeq'],
        'workspace-storage.js': ['RESULTS_CLEARED_KEY', 'FILES_CACHE_KEY', 'TONIES_REVERSE_VIEW_KEY'],
        'search-model.js': ['resultsCleared', 'selectingCandidateJobIds'],
        'local-uploads.js': ['queuedLocalUploads', 'localUploadQueuePumping'],
        'upload-tracking.js': ['uploadIntentsByJobId', 'pendingToniesUpload'],
        'tonie-library.js': ['toniesContentRequestSeq', 'toniesProcessingTimer'],
        'tonie-actions.js': ['toniesQueuedActions', 'finishedToniesActionIds'],
        'context-menus.js': ['toniesMenuChapterId', 'fileMenuFilename'],
        'tonies-picker.js': ['toniesImagesLoading', 'manualToniesRefreshInFlight'],
        'local-files.js': ['loadingFiles'],
        'workspace-jobs.js': ['jobStatusById', 'loadingJobs'],
        'realtime.js': ['isBackendOnline'],
    }
    for filename, names in owners.items():
        source = (ASSETS / filename).read_text()
        for name in names:
            assert re.search(rf"^(?:let|const) {name}\b", source, re.M), (filename, name)
    for filename, names in {
        'tonie-library.js': ['blockIfActionInProgress', 'isToniesActionBusy'],
        'app-core.js': ['lockApp'], 'local-files.js': ['downloadLocalFile'],
    }.items():
        source = (ASSETS / filename).read_text()
        for name in names:
            assert function_source(name).strip() in source
