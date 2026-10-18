"""Exact pre-refactor markup and interaction contracts for the Tonie track renderer."""
import json

import pytest

from test_frontend_contracts import run_js


SETUP = r"""
Date.now = () => 100000;
toniesReverseView = false;
toniesContentCache = {chapters: [
  {chapter_id:'a', title:'Song & <one> [oc:abcd]', duration:'01:02'},
  {chapter_id:'b', title:'', duration:'00:09'}
], free_minutes:87, total_minutes:90};
"""

CASES = {
    "empty": "toniesContentCache.chapters = [];",
    "loading": "toniesContentCache.chapters = []; loadingToniesContent = true;",
    "normal": "",
    "processing": "toniesContentCache.chapters[0].processing = true;",
    "edit": "toniesEditMode = true; toniesNameDrafts['id:a'] = 'Draft <&> name';",
    "delete": "toniesQueuedActions.set('delete-a', {url:selectedToniesUrl, type:'delete', params:{chapter_id:'a'}});",
    "placeholder": "pendingDropUploadIntent = {targetUrl:selectedToniesUrl, position:{before:'b'}, filename:'New <&>.mp3'};",
    "reverse_placeholder": "toniesReverseView = true; pendingDropUploadIntent = {targetUrl:selectedToniesUrl, position:{before:'b'}, filename:'New <&>.mp3'};",
    "selection": "trackSelectionMode = true; selectedTracks().add('b'); selectedTracks().add('stale');",
    "inline_error": "inlineRenames.set(selectedToniesUrl, {id:'a', draft:'Name <&>', error:'Refused <&>'});",
    "inline_busy": "inlineRenames.set(selectedToniesUrl, {id:'a', draft:'Name <&>', busy:true, submitted:true});",
    "highlight": "recentUploadedChapterIds = {a:99999, b:1};",
    "syncing": "toniesUploadSyncing = true;",
    "attached_syncing": "toniesUploadSyncing = true; activeUploadJobIds.add('job'); attachedUploadJobIds.add('job');",
    "unknown_capacity": "toniesContentCache.free_minutes = null; toniesContentCache.used_minutes = 18;",
}

# Captured from the original renderer before splitting; whitespace is intentional.
ROW_A = '''
    <div class="tonies-item   " draggable="true" data-source-index="0" data-chapter-id="a">
      <div class="drag-handle" style="opacity:1;" aria-label="Drag to arrange">⋮⋮</div>
      <div>
        <div class="truncate"><strong>Song &amp; &lt;one&gt;</strong></div>
        <div class="tiny">01:02</div>
      </div>
      <div class="row track-actions">
        <div class="tiny mono" title="Playback position 1">#1</div>
<button type="button" class="ghost track-overflow" aria-label="Actions for Song &amp; &lt;one&gt;" aria-haspopup="menu" >⋯</button>
      </div>
    </div>
  '''
ROW_B = (ROW_A.replace('data-source-index="0"', 'data-source-index="1"')
         .replace('data-chapter-id="a"', 'data-chapter-id="b"')
         .replace('Song &amp; &lt;one&gt;', 'Chapter 2').replace('01:02', '09')
         .replace('Playback position 1">#1', 'Playback position 2">#2'))
END_DROP = '<div class="tonies-end-drop-zone" data-end-drop-zone="1">Drop here to upload to bottom</div>'
SYNC = '<div class="tonies-sync-skeleton" title="Syncing with my.tonies.com…"></div>'
UPLOAD = '<div class="tonies-sync-skeleton" title="Uploading New &lt;&amp;&gt;.mp3…"></div>'
TITLE_A = '<div class="truncate"><strong>Song &amp; &lt;one&gt;</strong></div>'
TITLE_B = '<div class="truncate"><strong>Chapter 2</strong></div>'
HANDLE = '<div class="drag-handle" style="opacity:1;" aria-label="Drag to arrange">⋮⋮</div>'
INLINE = ('<input class="tonies-title-edit tonies-title-inline" aria-label="Track name" value="Name &lt;&amp;&gt;"  />'
          '<div class="row inline-rename-actions"><button class="inline-save" >Save</button>'
          '<button class="inline-cancel ghost">Cancel</button></div>')
EDIT_ROWS = ((ROW_A.replace(TITLE_A, '<input class="tonies-title-edit" data-source-index="0" value="Draft &lt;&amp;&gt; name" />')
              + ROW_B.replace(TITLE_B, '<input class="tonies-title-edit" data-source-index="1" value="Chapter 2" />'))
             .replace('draggable="true"', 'draggable="false"').replace('opacity:1;', 'opacity:0.35;')
             .replace('aria-haspopup="menu" >', 'aria-haspopup="menu" disabled>'))
HTML_SNAPSHOTS = {
    "empty": ('<div class="empty-tracks"><span class="empty-note" aria-hidden="true">♪</span>'
              '<h3>Room for a new favourite.</h3><p>No tracks yet. Choose YouTube or Local files in Add audio to get started.</p>'
              '<button class="ghost mobile-add" onclick="setWorkspaceView(\'add\', true)">Add audio</button></div>'),
    "loading": SYNC,
    "normal": ROW_A + ROW_B + END_DROP,
    "processing": ROW_A.replace('01:02', '<span class="loading-dot"></span>Processing audio…') + ROW_B + END_DROP,
    "edit": EDIT_ROWS,
    "delete": (ROW_A.replace('tonies-item   ', 'tonies-item  deleting ')
               .replace('aria-haspopup="menu" >', 'aria-haspopup="menu" disabled>') + ROW_B + END_DROP),
    "placeholder": ROW_A + UPLOAD + ROW_B + END_DROP,
    "reverse_placeholder": ROW_B + UPLOAD + ROW_A + END_DROP,
    "selection": (ROW_A.replace(HANDLE, '<label class="track-select-label"><input type="checkbox" class="track-select" aria-label="Select Song &amp; &lt;one&gt;"  /></label>')
                  + ROW_B.replace(HANDLE, '<label class="track-select-label"><input type="checkbox" class="track-select" aria-label="Select Chapter 2" checked /></label>') + END_DROP),
    "inline_error": ROW_A.replace(TITLE_A, INLINE + '<p class="error" role="alert">Refused &lt;&amp;&gt;</p>') + ROW_B + END_DROP,
    "inline_busy": (ROW_A.replace(TITLE_A, INLINE.replace('  />', ' readonly />')
                    .replace('class="inline-save" >Save', 'class="inline-save" disabled>Saving…')) + ROW_B + END_DROP),
    "highlight": (ROW_A.replace('tonies-item   ', 'tonies-item new-upload  ')
                  .replace('</strong>', '</strong> <span class="tiny badge-new">NEW</span>') + ROW_B + END_DROP),
    "syncing": SYNC + ROW_A + ROW_B + END_DROP,
    "attached_syncing": ROW_A + ROW_B + END_DROP,
    "unknown_capacity": ROW_A + ROW_B + END_DROP,
}
SNAPSHOTS = {state: {"html": html, "minutes": "87 min left • 3 min used", "progress": "3.3%"}
             for state, html in HTML_SNAPSHOTS.items()}
SNAPSHOTS["unknown_capacity"].update(minutes="Space syncing…", progress="20.0%")


@pytest.mark.parametrize("state", CASES)
def test_rendered_html_matches_original(state):
    run_js(SETUP + CASES[state] + "\nactualRenderToniesContent();\n"
           + "assert.deepEqual({html:toniesContentEl.innerHTML, minutes:toniesMinutesEl.textContent, "
           + "progress:toniesProgressEl.style.width}, " + json.dumps(SNAPSHOTS[state]) + ");")


EVENT_SETUP = SETUP + r"""
function eventNode() {
  const node = element(), handlers = new Map(), classes = new Set();
  node.addEventListener = (type, fn, options) => {
    if (!handlers.has(type)) handlers.set(type, []);
    handlers.get(type).push({fn, options});
  };
  node.emit = async (type, event = {}) => {
    event.target ??= node; event.currentTarget ??= node;
    event.preventDefault ??= () => { event.defaultPrevented = true; };
    event.stopPropagation ??= () => { event.stopped = true; };
    for (const {fn} of handlers.get(type) || []) await fn(event);
    return event;
  };
  node.classList = {add:c => classes.add(c), remove:c => classes.delete(c), contains:c => classes.has(c)};
  node.closest = selector => selector === '.tonies-item' ? node : null;
  node.getBoundingClientRect = () => ({right:300, bottom:150, top:100, height:50});
  return node;
}
const rows = toniesContentCache.chapters.map((chapter, index) => {
  const row = eventNode(), handle = eventNode(), input = eventNode(), select = eventNode(), menu = eventNode();
  row.dataset.sourceIndex = String(index);
  row.setAttribute('data-source-index', index); row.setAttribute('data-chapter-id', chapter.chapter_id);
  input.setAttribute('data-source-index', index);
  row.querySelector = s => ({'.drag-handle':handle, '.track-select':select, '.track-overflow':menu}[s] || null);
  return {row, handle, input, select, menu};
});
const end = eventNode();
toniesContentEl.querySelectorAll = selector => {
  if (selector === '.tonies-item') return rows.map(r => r.row);
  if (selector === '.tonies-title-edit') return rows.map(r => r.input);
  if (selector === '.tonies-item.drop-target') return rows.map(r => r.row).filter(r => r.classList.contains('drop-target'));
  return [];
};
toniesContentEl.querySelector = selector => selector === '[data-end-drop-zone="1"]' ? end : null;
const moves = [], uploads = [], menus = [];
reorderToniesChapter = async (...args) => moves.push(args);
uploadExistingToIndex = async (...args) => uploads.push(args);
openToniesMenu = (...args) => menus.push(args);
const dataTransfer = {types:[], getData:() => ''};
actualRenderToniesContent();
"""


@pytest.mark.parametrize("reverse", [False, True])
def test_desktop_reorder_and_external_drop_positions(reverse):
    run_js(EVENT_SETUP + f"toniesReverseView = {json.dumps(reverse)};" + r"""
await rows[0].row.emit('dragstart', {dataTransfer});
assert.equal(dataTransfer.effectAllowed, 'move');
await rows[1].row.emit('dragover', {dataTransfer});
assert.equal(dataTransfer.dropEffect, 'move');
assert.equal(rows[1].row.classList.contains('drop-target'), true);
await rows[1].row.emit('drop', {dataTransfer});
assert.deepEqual(moves, [[0, 1]]);
await rows[0].row.emit('dragend');
assert.equal(rows[0].row.classList.contains('dragging'), false);
assert.equal(rows[1].row.classList.contains('drop-target'), false);
dataTransfer.types = ['text/local-file']; dataTransfer.getData = () => 'Song.mp3';
await rows[1].row.emit('drop', {dataTransfer});
document.elementFromPoint = () => rows[1].row;
await toniesContentEl.ondrop({dataTransfer, preventDefault(){}});
document.elementFromPoint = () => null;
await toniesContentEl.ondrop({dataTransfer, preventDefault(){}});
await end.emit('dragover', {dataTransfer});
assert.equal(end.classList.contains('active'), true);
await end.emit('drop', {dataTransfer});
assert.equal(end.classList.contains('active'), false);
assert.deepEqual(uploads, toniesReverseView
  ? [['Song.mp3',2], ['Song.mp3',2], ['Song.mp3',0], ['Song.mp3',0]]
  : [['Song.mp3',1], ['Song.mp3',1], ['Song.mp3',2], ['Song.mp3',2]]);
toniesEditMode = true;
const blocked = await rows[0].row.emit('dragstart', {dataTransfer});
assert.equal(blocked.defaultPrevented, true);
await rows[1].row.emit('drop', {dataTransfer});
await end.emit('drop', {dataTransfer});
await toniesContentEl.ondrop({dataTransfer});
assert.equal(uploads.length, 4);
""")


def test_touch_drag_shares_indices_between_rows_and_finishes_once():
    run_js(EVENT_SETUP + r"""
await rows[0].handle.emit('touchstart');
document.elementFromPoint = () => rows[1].row;
const move = await rows[0].handle.emit('touchmove', {touches:[{clientX:20,clientY:30}]});
assert.equal(move.defaultPrevented, true);
assert.equal(rows[1].row.classList.contains('drop-target'), true);
await rows[0].handle.emit('touchend');
await rows[0].handle.emit('touchcancel');
assert.deepEqual(moves, [[0,1]]);
assert.equal(rows[0].row.classList.contains('dragging'), false);
assert.equal(rows[1].row.classList.contains('drop-target'), false);
await rows[1].handle.emit('touchstart');
document.elementFromPoint = () => rows[0].row;
await rows[1].handle.emit('touchmove', {touches:[{clientX:20,clientY:30}]});
await rows[1].handle.emit('touchcancel');
assert.deepEqual(moves, [[0,1], [1,0]]);
""")


def test_row_menu_long_press_double_tap_swipes_and_cancel():
    run_js(EVENT_SETUP + r"""
const row = rows[0].row, touch = {clientX:100,clientY:110};
await row.emit('contextmenu', touch);
assert.deepEqual(menus.pop(), [0,100,110]);
await row.emit('touchstart', {touches:[touch]});
const hold = [...timers.values()].find(t => t.ms === 520);
hold.fn();
assert.deepEqual(menus.pop(), [0,108,118]);
await row.emit('touchend', {changedTouches:[touch]});
assert.equal(menus.length, 0);
lastToniesTapIndex = null; lastToniesTapAt = 0;
for (let i=0; i<2; i++) {
  await row.emit('touchstart', {touches:[touch]});
  await row.emit('touchend', {changedTouches:[touch]});
}
assert.deepEqual(menus.pop(), [0,108,118]);
await row.emit('touchstart', {touches:[touch]});
await row.emit('touchmove', {touches:[{clientX:20,clientY:110}]});
await row.emit('touchend', {changedTouches:[{clientX:20,clientY:110}]});
assert.deepEqual(menus.pop(), [0,292,125]);
let closed = 0; closeToniesMenu = () => closed++;
await row.emit('touchstart', {touches:[touch]});
await row.emit('touchend', {changedTouches:[{clientX:180,clientY:110}]});
assert.equal(closed, 1);
await row.emit('touchstart', {touches:[touch]});
await row.emit('touchcancel');
assert.equal([...timers.values()].filter(t => t.ms === 520).length, 0);
toniesApplying = true;
await row.emit('contextmenu', touch);
await row.emit('touchstart', {touches:[touch]});
assert.equal(menus.length, 0);
""")


def test_selection_overflow_and_name_drafts_keep_row_identity():
    run_js(EVENT_SETUP + r"""
await rows[1].select.emit('change', {target:{checked:true}});
assert.deepEqual([...selectedTracks()], ['b']);
assert.equal(document.getElementById('track-selection-count').textContent, '1 selected');
await rows[1].menu.emit('click');
assert.equal(menuReturnFocusId, 'b');
assert.deepEqual(menus, [[1,92,154]]);
rows[1].input.value = 'Changed';
await rows[1].input.emit('input');
assert.equal(toniesNameDrafts['id:b'], 'Changed');
""")


def test_drag_state_is_shared_within_a_render_but_reset_on_rerender():
    run_js(EVENT_SETUP + r"""
await rows[0].handle.emit('touchstart');
document.elementFromPoint = () => rows[1].row;
await rows[1].handle.emit('touchmove', {touches:[{clientX:20,clientY:30}]});
await rows[1].handle.emit('touchend');
assert.deepEqual(moves, [[0,1]]);
await rows[0].row.emit('dragstart', {dataTransfer});
await rows[0].handle.emit('touchstart');
// The browser replaces row nodes when innerHTML is assigned. Do the same here.
for (const controls of rows) {
  const row = eventNode(), handle = eventNode();
  row.dataset.sourceIndex = controls.row.dataset.sourceIndex;
  row.setAttribute('data-chapter-id', controls.row.getAttribute('data-chapter-id'));
  row.querySelector = s => s === '.drag-handle' ? handle : null;
  Object.assign(controls, {row, handle, input:eventNode()});
}
actualRenderToniesContent();
await rows[1].row.emit('drop', {dataTransfer});
await rows[1].handle.emit('touchmove', {touches:[{clientX:20,clientY:30}]});
await rows[1].handle.emit('touchend');
assert.deepEqual(moves, [[0,1]], 'a new render must not inherit either drag');
""")


def test_inline_focus_caret_and_edit_handlers_survive_render():
    run_js(SETUP + r"""
const row = element(), input = element(), oldInput = element(), save = element(), cancel = element();
row.setAttribute('data-chapter-id', 'a');
row.querySelector = s => ({'.tonies-title-inline':input, '.inline-save':save, '.inline-cancel':cancel}[s] || null);
toniesContentEl.querySelectorAll = s => s === '.tonies-item' ? [row] : s === '.tonies-title-edit' ? [input] : [];
toniesContentEl.querySelector = s => s === '.tonies-title-inline' ? input : null;
oldInput.classList.contains = s => s === 'tonies-title-inline';
oldInput.selectionStart = 2; oldInput.selectionEnd = 5;
document.activeElement = oldInput;
let caret, saves = 0, cancels = 0;
input.setSelectionRange = (...args) => { caret = args; };
saveInlineRename = () => saves++;
cancelInlineRename = () => cancels++;
inlineRenames.set(selectedToniesUrl, {id:'a', draft:'Old name'});
actualRenderToniesContent();
assert.equal(document.activeElement, input);
assert.deepEqual(caret, [2,5]);
input.value = 'New name'; input.dispatchEvent({type:'input'});
assert.equal(getInlineRename().draft, 'New name');
input.dispatchEvent({type:'keydown', key:'Enter', preventDefault(){}});
input.dispatchEvent({type:'keydown', key:'Escape', preventDefault(){}, stopPropagation(){}});
save.dispatchEvent({type:'click'}); cancel.dispatchEvent({type:'click'});
assert.equal(saves, 2); assert.equal(cancels, 2);
""")

