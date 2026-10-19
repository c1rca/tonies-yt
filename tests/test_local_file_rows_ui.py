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
assert.match(rows[0], /aria-label="Play Moon.mp3"/);
assert.match(rows[0], /aria-label="Add Moon.mp3 to Fox"/);
assert.match(rows[0], /aria-label="More actions for Moon.mp3"/);
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
assert.equal(play.getAttribute('aria-label'), 'Pause Moon.mp3');
document.getElementById('local-audio').pause();
assert.equal(play.dataset.state, 'paused');
assert.equal(play.getAttribute('aria-label'), 'Play Moon.mp3');
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
