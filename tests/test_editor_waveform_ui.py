"""The editor's waveform: SoundCloud-style bars with gaps, real dynamics and a soft reflection."""
from page_sources import WEB, function_source
from test_frontend_contracts import run_js as run_page_js


def run_js(body):
    # Renderer unit/bridge tests use the real module; parent owns the page script insertion.
    renderer = (WEB / "assets/editor-waveform.js").read_text(encoding="utf-8")
    return run_page_js(renderer + "\nglobalThis.waveformBars = waveformBars; globalThis.renderEditorBars = renderEditorBars;\n" + body)

CANVAS = r"""
const rects = [];
const canvas = document.getElementById('editor-canvas');
let fill = '', alpha = 1;
const context = {clearRect(){}, beginPath(){}, roundRect(){}, fill(){}, set fillStyle(v){ fill = v; }, get fillStyle(){ return fill; },
  set globalAlpha(v){ alpha = v; }, get globalAlpha(){ return alpha; },
  fillRect(x, y, w, h){ rects.push({x, y, w, h, fill, alpha}); }};
canvas.getContext = () => context; canvas.clientWidth = 300; canvas.clientHeight = 100;
global.getComputedStyle = () => ({getPropertyValue: name => ({'--accent':'A', '--muted':'M', '--text':'T'}[name] || '')});
window.devicePixelRatio = 1; editorState.ready = true;
"""


def test_bars_follow_loudness_on_a_decibel_scale():
    run_js("""
const bars = waveformBars([0.5, 0.5, 0.05, 0.05, 0.005, 0.005, 0, 0], 4);
assert.equal(bars.length, 4);
assert.equal(bars[0], 1);                       // the fixed -6 dB reference fills the height
assert.ok(bars[1] > 0.2 && bars[1] < 0.8);      // 20 dB quieter: clearly lower, still visible
assert.ok(bars[2] < bars[1] && bars[2] > 0);    // 40 dB quieter: a stub
assert.equal(bars[3], 0);                        // silence
assert.deepEqual(waveformBars([], 5), [0, 0, 0, 0, 0]);
// Each bar takes the loudest slice it covers, so short loud moments aren't averaged away.
assert.equal(waveformBars([0.01, 0.5, 0.01, 0.01], 2)[0], 1);
""")


def test_waveform_is_drawn_as_separate_bars_with_a_reflection():
    run_js(CANVAS + """
editorState.levels = Array(1000).fill(0.3); editorState.peaks = Array(1000).fill(0.9);
editorState.view = {start: 0, end: 100}; editorState.seconds = 100;
editorState.selection = {start: 25, end: 75};
drawEditorWaveform();
const top = rects.filter(r => r.alpha === 1 && r.fill !== 'T'), below = rects.filter(r => r.alpha < 1);
assert.equal(top.length, 100);                       // 300 px / (2 px bar + 1 px gap)
assert.equal(below.length, top.length);              // every bar has a reflection
assert.ok(top.every(r => r.w === 2));
assert.ok(top.every((r, i) => i === 0 || r.x - top[i - 1].x === 3), 'a 1 px gap between bars');
const baseline = top[0].y + top[0].h;
assert.ok(baseline > 60 && baseline < 80, 'bars stand on a line about 70% down');
assert.ok(below.every(r => r.y >= baseline && r.h <= top[0].h / 2));
// Inside the clip: the accent colour; outside it: muted.
assert.equal(top[50].fill, 'A'); assert.equal(top[5].fill, 'M'); assert.equal(top[95].fill, 'M');
""")


def test_older_outlines_without_levels_still_draw():
    run_js(CANVAS + """
editorState.levels = null; editorState.peaks = Array(1000).fill(0.4);
editorState.view = {start: 0, end: 10}; editorState.seconds = 10; editorState.selection = {start: 0, end: 10};
drawEditorWaveform();
assert.ok(rects.filter(r => r.fill !== 'T').length > 0 && rects.every(r => r.h > 0));
""")


def test_editor_keeps_the_levels_it_loads():
    assert "editorState.levels = outline.levels" in function_source("loadEditorWaveform")


def test_dpr_resize_zoom_selection_and_playhead_share_css_pixel_coordinates():
    run_js(CANVAS + """
editorState.levels = [0.5,0.05,0.005,0]; editorState.peaks = [1,1,1,0];
editorState.view = {start:30,end:90}; editorState.selection = {start:45,end:75};
editorState.seconds = 120; document.getElementById('editor-audio').currentTime = 60;
window.devicePixelRatio = 2;
drawEditorWaveform();
assert.equal(canvas.width,600); assert.equal(canvas.height,200);
const top = rects.filter(r => r.alpha === 1 && r.fill !== 'T');
assert.equal(top.length,100); assert.ok(top.every(r => r.w === 4));
assert.equal(top[50].fill,'A'); assert.equal(top[5].fill,'M');
assert.ok(top[0].h > top[30].h && top[30].h > top[60].h && top[60].h > top[90].h);
assert.deepEqual(rects.find(r => r.fill === 'T'), {x:298,y:0,w:4,h:200,fill:'T',alpha:1});
rects.length=0; canvas.clientWidth=600; window.devicePixelRatio=1;
drawEditorWaveform();
assert.equal(canvas.width,600); assert.equal(canvas.height,100);
assert.equal(rects.filter(r => r.alpha === 1 && r.fill !== 'T').length,200);
assert.equal(rects.find(r => r.fill === 'T').x,299);
// Zoom is horizontal only: the same RMS has exactly the same height.
const unzoomedHeight=rects.find(r => r.fill !== 'T').h;
rects.length=0; editorState.view={start:45,end:75}; drawEditorWaveform();
assert.equal(rects.find(r => r.fill !== 'T').h,unzoomedHeight);
assert.equal(rects.find(r => r.fill === 'T').x,299);
""")


def test_silence_and_invalid_values_do_not_create_nonfinite_or_invented_tall_bars():
    run_js(CANVAS + """
assert.deepEqual(waveformBars([NaN,Infinity,-1,0],4),[0,0,0,0]);
editorState.levels = [0,0,0,0]; editorState.peaks = [0,0,0,0];
editorState.view={start:0,end:10}; editorState.selection={start:0,end:10};
drawEditorWaveform();
assert.ok(rects.filter(r => r.fill !== 'T').every(r => r.h === 1), 'only a one-pixel silence baseline');
assert.ok(rects.every(r => [r.x,r.y,r.w,r.h].every(Number.isFinite)));
rects.length=0; canvas.clientWidth=0; drawEditorWaveform(); assert.deepEqual(rects,[]);
""")
