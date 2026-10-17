/** Workspace views, source tabs, target identity and appearance. No media remounts. */
// Workspace navigation changes visibility, never remounts sources or the player.
let workspaceView = 'tracks';
let workspaceSource = 'youtube';
function setWorkspaceView(view, focus = false) {
  if (!['tracks', 'add', 'activity'].includes(view)) return;
  workspaceView = view;
  document.getElementById('workspace').setAttribute('data-view', view);
  document.getElementById('view-tracks').hidden = view === 'activity';
  document.getElementById('view-activity').hidden = view !== 'activity';
  for (const name of ['tracks', 'add', 'activity']) {
    const button = document.getElementById('nav-' + name);
    if (name === view) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  closeToniesMenu(); closeFileMenu();
  if (focus) {
    document.getElementById('view-' + view).focus({preventScroll:true});
    window.scrollTo({top:0, behavior:'instant'});
  }
}
function setWorkspaceSource(source, focus = false) {
  if (!['youtube', 'local'].includes(source)) return;
  workspaceSource = source;
  for (const name of ['youtube', 'local']) {
    document.getElementById('source-' + name).hidden = name !== source;
    const tab = document.getElementById('source-tab-' + name);
    tab.setAttribute('aria-selected', String(name === source));
    tab.setAttribute('tabindex', name === source ? '0' : '-1');
  }
  if (focus) document.getElementById('source-tab-' + source).focus();
}
function setWorkspaceTheme(theme) {
  if (!['system', 'light', 'dark'].includes(theme)) return;
  document.documentElement.setAttribute('data-theme', theme);
  document.getElementById('workspace-theme').value = theme;
  try { localStorage.setItem('tonies-auto.workspace-theme.v1', theme); } catch {}
}
function loadWorkspaceTheme() {
  let theme = 'system';
  try { theme = localStorage.getItem('tonies-auto.workspace-theme.v1') || theme; } catch {}
  setWorkspaceTheme(['system', 'light', 'dark'].includes(theme) ? theme : 'system');
}
function renderWorkspaceTonieRail(container) {
  const items = [...toniesCache];
  container.innerHTML = items.map((tonie, index) => {
    const selected = selectedToniesUrl === tonie.edit_url || (!selectedToniesUrl && index === 0);
    const image = safeImageSrc(toniesImageCache[tonie.edit_url] || tonie.image);
    const content = getCachedToniesContent(tonie.edit_url);
    const count = Array.isArray(content?.chapters) ? content.chapters.length : null;
    return `<button type="button" class="tonies-row ${selected ? 'active' : ''}" data-tonie-index="${index}" aria-pressed="${selected}">
      <span>${image ? `<img src="${esc(image)}" alt="" />` : '<span class="tonie-art-fallback" aria-hidden="true">♪</span>'}</span>
      <span class="tonie-row-copy"><span class="truncate">${esc(tonie.name)}</span><span class="tiny">${count === null ? 'Ready to add audio' : `${count} ${count === 1 ? 'track' : 'tracks'}`}</span></span>
    </button>`;
  }).join('');
  for (const button of container.querySelectorAll('[data-tonie-index]')) {
    button.addEventListener('click', () => {
      const target = items[Number(button.getAttribute('data-tonie-index'))];
      if (target) selectTonies(target.edit_url);
    });
  }
}

function workspaceTargetName(url = selectedToniesUrl) {
  return toniesCache.find(t => t.edit_url === url)?.name || 'this Tonie';
}
function safeImageSrc(src) {
  const value = String(src || '');
  if (/^\/(?!\/)/.test(value) || /^data:image\/(png|jpeg|webp|gif);base64,[a-z0-9+/=]+$/i.test(value)) return value;
  try { const u = new URL(value); return ['https:', 'http:'].includes(u.protocol) && !u.username && !u.password ? u.href : ''; } catch { return ''; }
}
function renderWorkspaceTarget() {
  const target = toniesCache.find(t => t.edit_url === selectedToniesUrl);
  document.getElementById('selected-tonie-name').textContent = target?.name || 'Choose a Tonie';
  const image = safeImageSrc(toniesImageCache[selectedToniesUrl] || target?.image);
  document.getElementById('selected-tonie-art').innerHTML = image ? `<img src="${esc(image)}" alt="" />` : '';
  document.getElementById('add-destination').textContent = target ? `Add to ${target.name}` : 'Choose a Tonie to add audio.';
  renderLocalSelection();
  renderWorkspaceUndo();
}
function toggleTonieSelector(force) {
  const button = document.getElementById('change-tonie');
  const wasOpen = button.getAttribute('aria-expanded') === 'true';
  const open = typeof force === 'boolean' ? force : !wasOpen;
  document.getElementById('tonie-rail').classList.toggle('selector-open', open);
  button.setAttribute('aria-expanded', String(open));
  if (open) document.getElementById('tonies').querySelector('button')?.focus();
  else if (wasOpen) button.focus();
}
for (const source of ['youtube', 'local']) document.getElementById('source-tab-' + source).addEventListener('keydown', e => {
  if (['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(e.key)) {
    e.preventDefault(); setWorkspaceSource(e.key === 'Home' ? 'youtube' : e.key === 'End' ? 'local' : source === 'local' ? 'youtube' : 'local', true);
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { toggleTonieSelector(false); closeToniesMenu(); closeFileMenu(); }
});
