"""Local files rows: one-tap Play and Add, everything else in the row's actions menu."""
from html.parser import HTMLParser

from page_sources import HTML, page_css
from test_frontend_contracts import run_js

FILES = """
toniesCache = [{edit_url:'tonie-a', name:'Fox'}];
filesCache = [{name:'Moon.mp3', size_bytes:24576, duration_sec:3},
              {name:'Baby_Shark_Dance.mp3', size_bytes:2463076, duration_sec:154}];
renderFiles = actualRenderFiles;
const rowsOf = html => html.split('<article').slice(1);
const count = (html, re) => (html.match(re) || []).length;
const settle = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const passUndoWindow = () => { for (const [id, t] of [...timers]) if (t.ms === 2400) { timers.delete(id); t.fn(); } };
"""


def test_each_row_offers_play_add_and_one_actions_menu_only():
    run_js(FILES + r"""
renderFiles();
const rows = rowsOf(filesEl.innerHTML);
assert.equal(rows.length, 2);
for (const row of rows) {
  assert.equal(count(row, /<button/g), 3, row);
  assert.match(row, /class="[^"]*local-play-btn[^"]*"[^>]*data-state="paused"/);
  assert.match(row, /class="[^"]*local-upload-btn/);
  assert.match(row, /class="[^"]*local-more-btn[^"]*"[^>]*aria-haspopup="menu"/);
  assert.doesNotMatch(row, /local-edit-btn|trash-btn|type="checkbox"/);
}
assert.match(rows[0], /aria-label="Play Moon"/);
assert.match(rows[0], /aria-label="Add Moon to Fox"/);
assert.match(rows[0], /aria-label="More actions for Moon"/);
assert.doesNotMatch(filesEl.innerHTML, /onclick=/);
""")


def test_rows_show_the_track_title_and_readable_size():
    run_js(FILES + r"""
renderFiles();
const rows = rowsOf(filesEl.innerHTML);
// The title a Tonie track gets: underscores as spaces, no file extension.
assert.match(rows[0], /<strong>Moon<\/strong>/);
assert.match(rows[1], /<strong>Baby Shark Dance<\/strong>/);
assert.match(rows[0], /title="Moon.mp3"/);
assert.match(rows[0], /0:03 · 24 KB/);
assert.match(rows[1], /2:34 · 2.3 MB/);
assert.equal(fmtFileSize(0), '0 KB');
assert.equal(fmtFileSize(300), '1 KB');
assert.equal(fmtFileSize(1048576 * 12.25), '12.3 MB');
assert.equal(localFileTitle('A_b.c.MP3'), 'A b.c');
assert.equal(localFileTitle('notes.flac'), 'notes');
""")


def test_select_mode_swaps_row_actions_for_checkboxes_and_cancel_clears():
    run_js(FILES + r"""
const select = document.getElementById('select-local-files');
const bar = document.getElementById('local-selection-bar');
renderFiles();
assert.equal(bar.hidden, true);
assert.equal(select.textContent, 'Select');
toggleLocalSelectionMode();
assert.equal(localSelectionMode, true);
assert.equal(select.textContent, 'Cancel selection');
assert.equal(bar.hidden, false);
for (const row of rowsOf(filesEl.innerHTML)) {
  assert.match(row, /type="checkbox" class="local-file-select"/);
  assert.match(row, /local-play-btn/);
  assert.doesNotMatch(row, /local-upload-btn|local-more-btn/);
}
toggleLocalSelection('Moon.mp3', true);
assert.equal(document.getElementById('local-selection-count').textContent, '1 selected');
assert.equal(document.getElementById('delete-selected-local').disabled, false);
toggleLocalSelectionMode();
assert.equal(localSelectionMode, false);
assert.equal(localSelectedFiles.size, 0);
assert.equal(bar.hidden, true);
""")


def test_select_all_takes_only_the_files_the_filter_shows():
    run_js(FILES + r"""
toggleLocalSelectionMode();
fileFilterEl.value = 'shark';
selectAllLocalFiles();
assert.deepEqual([...localSelectedFiles], ['Baby_Shark_Dance.mp3']);
fileFilterEl.value = '';
selectAllLocalFiles();
assert.equal(localSelectedFiles.size, 2);
assert.equal(document.getElementById('local-selection-count').textContent, '2 selected');
""")


def test_adding_the_selection_leaves_select_mode():
    run_js(FILES + r"""
toggleLocalSelectionMode();
toggleLocalSelection('Moon.mp3', true);
const calls = [];
uploadExisting = async (name, options) => { calls.push([name, options.targetUrl]); };
await addSelectedLocalFiles();
assert.deepEqual(calls, [['Moon.mp3', 'tonie-a']]);
assert.equal(localSelectionMode, false);
assert.equal(document.getElementById('local-selection-bar').hidden, true);
""")


def test_bulk_delete_confirms_once_then_deletes_each_file_after_the_undo_window():
    run_js(FILES + r"""
let confirmation, refreshes = 0; const deleted = [];
askConfirm = async options => { confirmation = options; return true; };
refreshFiles = async () => { refreshes++; };
global.fetch = async (path, options) => { assert.equal(options.method, 'DELETE'); deleted.push(path); return {ok:true}; };
await toggleLocalPlayback('Moon.mp3');
toggleLocalSelectionMode();
selectAllLocalFiles();
const done = deleteSelectedLocalFiles();
await settle();
assert.equal(confirmation.title, 'Delete 2 local files?');
assert.match(confirmation.message, /Moon.mp3\nBaby_Shark_Dance.mp3/);
assert.match(confirmation.message, /cannot be undone/);
assert.equal(confirmation.confirmText, 'Delete files');
assert.deepEqual(deleted, []); // Nothing goes until the undo window passes.
passUndoWindow();
await done;
assert.deepEqual(deleted, ['/api/files/Moon.mp3', '/api/files/Baby_Shark_Dance.mp3']);
assert.equal(refreshes, 1);
assert.equal(localPlaybackFilename, ''); // The playing file was deleted.
assert.equal(localSelectionMode, false);
assert.equal(localSelectedFiles.size, 0);
""")


def test_delete_undo_keeps_files_and_a_refused_confirmation_sends_nothing():
    run_js(FILES + r"""
const deleted = []; let undo;
global.fetch = async path => { deleted.push(path); return {ok:true}; };
showToast = (message, kind, ms, action) => { if (action) undo = action.onClick; return () => {}; };
askConfirm = async () => false;
await deleteFile('Moon.mp3');
assert.equal(undo, undefined);
askConfirm = async () => true;
const done = deleteFile('Moon.mp3');
await settle();
undo();
passUndoWindow();
await done;
assert.deepEqual(deleted, []);
""")


def test_failed_deletes_are_reported_and_the_rest_still_go():
    run_js(FILES + r"""
const toasts = [];
showToast = (message, kind) => { toasts.push([message, kind]); return () => {}; };
global.fetch = async path => ({ok: !path.includes('Moon')});
const done = deleteLocalFiles(['Moon.mp3', 'Baby_Shark_Dance.mp3']);
await settle();
passUndoWindow();
await done;
assert.deepEqual(toasts.at(-1), ['Could not delete 1 of 2 local files. Refresh and try again.', 'err']);
""")


def test_actions_menu_opens_at_its_button_and_each_item_acts_on_that_file():
    run_js(FILES + r"""
initContextMenus();
const button = element();
button.getBoundingClientRect = () => ({right:500, bottom:300, top:260, width:44});
const calls = [];
openAudioEditorForFile = name => calls.push(['edit', name]);
deleteFile = name => calls.push(['delete', name]);
uploadExistingToIndex = (name, index) => calls.push(['add', name, index]);
toniesContentCache = {chapters:[{}, {}, {}]}; toniesReverseView = false;
const open = () => openFileMenuFrom(button, 'Moon.mp3');
open();
assert.equal(fileMenuFilename, 'Moon.mp3');
assert.equal(fileMenuEl.getAttribute('aria-hidden'), 'false');
assert.equal(document.activeElement, document.getElementById('file-menu-edit'));
document.getElementById('file-menu-edit').onclick();
assert.equal(fileMenuEl.getAttribute('aria-hidden'), 'true');
assert.equal(document.activeElement, button); // Focus returns to the row's button.
open(); await document.getElementById('file-menu-upload-top').onclick();
open(); await document.getElementById('file-menu-upload-bottom').onclick();
open(); document.getElementById('file-menu-delete').onclick();
assert.deepEqual(calls, [['edit', 'Moon.mp3'], ['add', 'Moon.mp3', 0], ['add', 'Moon.mp3', 3], ['delete', 'Moon.mp3']]);
""")


def test_play_button_state_follows_the_one_player():
    run_js(FILES + r"""
const play = element(), row = element();
row.setAttribute('data-file-name', 'Moon.mp3');
row.querySelector = s => (s === '.local-play-btn' ? play : null);
filesEl.querySelectorAll = () => [row];
await toggleLocalPlayback('Moon.mp3');
assert.equal(play.dataset.state, 'playing');
assert.equal(play.getAttribute('aria-label'), 'Pause Moon');
document.getElementById('local-audio').pause();
assert.equal(play.dataset.state, 'paused');
assert.equal(play.getAttribute('aria-label'), 'Play Moon');
""")


def test_filter_prunes_selection_before_render_and_before_bulk_actions():
    run_js(FILES + """
toggleLocalSelectionMode(); selectAllLocalFiles();
fileFilterEl.value = 'shark'; renderFiles();
assert.deepEqual([...localSelectedFiles], ['Baby_Shark_Dance.mp3']);
assert.equal(document.getElementById('local-selection-count').textContent, '1 selected');
fileFilterEl.value = 'Moon'; // An action must also reconcile before consuming names.
const calls = []; uploadExisting = async name => calls.push(name);
await addSelectedLocalFiles();
assert.deepEqual(calls, []);
assert.equal(localSelectedFiles.size, 0);
""")


def test_select_all_toggles_clear_and_empty_library_disables_select():
    run_js(FILES + """
toggleLocalSelectionMode(); selectAllLocalFiles();
assert.equal(document.getElementById('select-all-local').textContent, 'Clear selection');
selectAllLocalFiles(); assert.equal(localSelectedFiles.size, 0);
assert.equal(document.getElementById('select-all-local').textContent, 'Select all');
filesCache = []; renderFiles();
assert.equal(document.getElementById('select-local-files').disabled, true);
""")


def test_delete_renders_busy_and_retains_failed_selection_after_partial_refusal():
    run_js(FILES + """
toggleLocalSelectionMode(); selectAllLocalFiles();
let answer; askConfirm = () => new Promise(r => answer = r);
global.fetch = async path => ({ok: !path.includes('Moon')});
const done = deleteSelectedLocalFiles();
assert.match(filesEl.innerHTML, /class="local-file-select"[^>]*disabled/);
assert.equal(document.getElementById('select-local-files').disabled, true);
toggleLocalSelection('Moon.mp3', false);
assert.equal(localSelectedFiles.size, 2);
answer(true); await settle();
assert.match(filesEl.innerHTML, /class="local-file-select"[^>]*disabled/);
passUndoWindow(); await done;
assert.equal(localSelectionSubmitting, false);
assert.equal(localSelectionMode, true);
assert.deepEqual([...localSelectedFiles], ['Moon.mp3']);
assert.doesNotMatch(filesEl.innerHTML, /class="local-file-select"[^>]*disabled/);
""")


def test_all_delete_failures_preserve_selection_and_refresh_failure_still_reports_outcome():
    run_js(FILES + """
toggleLocalSelectionMode(); selectAllLocalFiles();
const toasts = [];
showToast = (message, kind) => { toasts.push([message, kind]); return () => {}; };
global.fetch = async () => ({ok:false});
let done = deleteSelectedLocalFiles(); await settle(); passUndoWindow(); await done;
assert.equal(localSelectionMode, true); assert.equal(localSelectedFiles.size, 2);
assert.match(toasts.at(-1)[0], /Could not delete 2 of 2/);
global.fetch = async () => ({ok:true});
refreshFiles = async () => { throw Error('Offline'); };
done = deleteSelectedLocalFiles(); await settle(); passUndoWindow(); await done;
assert.equal(localSelectedFiles.size, 0); assert.equal(localSelectionMode, false);
assert.match(toasts.at(-1)[0], /deleted.*refresh/i);
assert.equal(filesCache.length, 0); // A stale refresh cannot revive known deleted files.
""")


def test_delete_undo_announces_kept_files_and_preserves_selection():
    run_js(FILES + """
toggleLocalSelectionMode(); selectAllLocalFiles();
let undo; const messages = [];
showToast = (message, kind, ms, action) => { messages.push(message); if (action) undo = action.onClick; return () => {}; };
const done = deleteSelectedLocalFiles(); await settle(); undo(); passUndoWindow(); await done;
assert.equal(localSelectionMode, true); assert.equal(localSelectedFiles.size, 2);
assert.match(messages.at(-1), /kept/i);
""")


def test_context_menu_restores_row_trigger_and_same_button_toggles_menu():
    run_js(FILES + """
const row = element(), more = element(), play = element();
row.setAttribute('data-file-name', 'Baby_Shark_Dance.mp3');
row.querySelector = s => s === '.local-more-btn' ? more : s === '.local-play-btn' ? play : null;
filesEl.querySelectorAll = () => [row];
more.getBoundingClientRect = () => ({right:300, bottom:300, top:260});
bindLocalFileRow(row);
row.dispatchEvent({type:'contextmenu', clientX:20, clientY:30, preventDefault(){}});
assert.equal(fileMenuReturnFocus, more);
assert.equal(more.getAttribute('aria-expanded'), 'true');
assert.equal(fileMenuEl.getAttribute('aria-label'), 'Actions for Baby Shark Dance');
closeFileMenu(); assert.equal(document.activeElement, more);
assert.equal(more.getAttribute('aria-expanded'), 'false');
openFileMenuFrom(more, 'Baby_Shark_Dance.mp3');
openFileMenuFrom(more, 'Baby_Shark_Dance.mp3');
assert.equal(fileMenuEl.getAttribute('aria-hidden'), 'true');
row.dispatchEvent({type:'keydown', key:'F10', shiftKey:true, preventDefault(){}});
assert.equal(fileMenuReturnFocus, more);
""")


def test_menu_delete_moves_focus_to_next_row_or_filter_after_rerender():
    run_js(FILES + """
const first = element(), next = element(), trigger = element(), nextButton = element();
first.setAttribute('data-file-name', 'Moon.mp3'); next.setAttribute('data-file-name', 'Baby_Shark_Dance.mp3');
first.querySelector = () => trigger; next.querySelector = () => nextButton;
filesEl.querySelectorAll = () => filesCache.some(f => f.name === 'Moon.mp3') ? [first, next] : [next];
global.fetch = async () => ({ok:true});
trigger.focus();
let done = deleteFile('Moon.mp3'); await settle(); passUndoWindow(); await done;
assert.equal(document.activeElement, nextButton);
filesEl.querySelectorAll = () => [];
nextButton.focus(); done = deleteFile('Baby_Shark_Dance.mp3');
await settle(); passUndoWindow(); await done;
assert.equal(document.activeElement, fileFilterEl);
""")


def test_touch_hold_opens_menu_but_scroll_and_short_tap_do_not():
    run_js(FILES + """
const row = element(), more = element();
row.setAttribute('data-file-name', 'Moon.mp3'); row.querySelector = () => more;
bindLocalFileRow(row);
const start = () => row.dispatchEvent({type:'touchstart', touches:[{clientX:20, clientY:30}]});
start(); row.dispatchEvent({type:'touchend'});
assert.equal([...timers.values()].some(t => t.ms === 550), false);
start(); row.dispatchEvent({type:'touchmove'});
assert.equal([...timers.values()].some(t => t.ms === 550), false);
start(); const hold = [...timers.values()].find(t => t.ms === 550);
assert.ok(hold, 'A real touch hold must open the menu'); hold.fn();
assert.equal(fileMenuFilename, 'Moon.mp3'); assert.equal(fileMenuReturnFocus, more);
""")


def test_row_control_names_match_visible_title_and_escape_markup():
    run_js(FILES + """
const html = localFileRowHtml({name:'Baby_Shark_Dance.mp3'});
for (const label of ['Play Baby Shark Dance', 'Add Baby Shark Dance to Fox', 'More actions for Baby Shark Dance'])
  assert.ok(html.includes(`aria-label="${label}"`), label);
assert.match(html, /aria-expanded="false"/);
assert.ok(localFileRowHtml({name:'<x>_&.mp3'}).includes('Play &lt;x&gt; &amp;'));
""")


class _Menu(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth, self.items, self.attrs = 0, [], {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "file-menu":
            self.depth, self.attrs = 1, attrs
        elif self.depth and tag == "button":
            self.items.append(attrs)


def test_file_menu_markup_is_an_accessible_menu_with_delete_last():
    menu = _Menu()
    menu.feed(HTML.read_text(encoding="utf-8"))
    assert menu.attrs.get("role") == "menu" and menu.attrs.get("aria-label") == "File actions"
    ids = [item["id"] for item in menu.items]
    assert ids == ["file-menu-edit", "file-menu-upload-top", "file-menu-upload-bottom",
                   "file-menu-download", "file-menu-delete", "file-menu-cancel"]
    assert all(item.get("role") == "menuitem" and item.get("type") == "button" for item in menu.items)
    assert "danger" in menu.items[4].get("class", "")


def test_local_rows_are_compact_with_touch_sized_targets():
    css = page_css()
    for selector in (".local-play-btn", ".local-more-btn", ".file.is-current"):
        assert selector in css
    assert '.local-play-btn[data-state="playing"] .icon-play' in css
    # The selected-row override must never put a playing icon on the same raised surface.
    assert '.file.selecting:has(.local-file-select:checked) .local-play-btn:not(:hover):not([data-state="playing"])' in css
    assert '.local-upload-btn:disabled,.local-upload-btn:disabled:hover' in css
    coarse = css[css.index('@media(pointer:coarse)'):]
    assert 'min-width:44px' in coarse and 'min-height:44px' in coarse
