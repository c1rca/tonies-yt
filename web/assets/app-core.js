/** Small shared helpers: escaping, formatting, the API call, toasts and status lines. */
function initAppCore() {
  if (lockAppEl) lockAppEl.onclick = lockApp;
  document.addEventListener('pointerdown', (e) => {
    const btn = e.target.closest?.('button');
    if (!btn) return;
    btn.classList.remove('tap-feedback');
    void btn.offsetWidth;
    btn.classList.add('tap-feedback');
    setTimeout(() => btn.classList.remove('tap-feedback'), 180);
  });
}

function esc(s) {
  return String(s ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function fmtDuration(sec) {
  if (sec == null || Number.isNaN(Number(sec))) return '--:--';
  const n = Math.max(0, Math.round(Number(sec)));
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  const s = n % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  return `${m}:${String(s).padStart(2,'0')}`;
}

function fmtChapterDuration(s) {
  const v = String(s || '').trim();
  return v.startsWith('00:') ? v.slice(3) : v;
}

function uiText(s) {
  return String(s || '').replaceAll('_', ' ');
}

function classifyError(msg) {
  const m = String(msg || '').toLowerCase();
  if (m.includes('not available') || m.includes('video unavailable')) return 'Video unavailable';
  if (m.includes('private')) return 'Private video';
  if (m.includes('members-only') || m.includes('membership')) return 'Members-only video';
  if (m.includes('age') || m.includes('sign in')) return 'Age/login restricted';
  if (m.includes('timeout') || m.includes('timed out')) return 'Network timeout';
  if (m.includes('signature') || m.includes('nsig') || m.includes('precondition')) return 'YouTube extraction/signature issue';
  if (m.includes('requested format is not available') || m.includes('only images')) return 'No playable audio format';
  return 'Upload failed';
}

function getJobStatus(jobOrStatus) {
  return String(typeof jobOrStatus === 'string' ? jobOrStatus : (jobOrStatus?.status || ''));
}

// tone: '' (in progress, muted), 'ok' or 'err'; placement 'above' sits over a candidate row.
function statusLine(text, { tone = '', busy = false, placement = '' } = {}) {
  const classes = ['tiny', 'status-line', tone && `status-${tone}`, placement].filter(Boolean).join(' ');
  return `<div class="${classes}">${busy ? '<span class="loading-dot"></span>' : ''}${esc(text)}</div>`;
}

async function lockApp() {
  stopLocalPlayback();
  try {
    await api('/api/setup/lock', { method: 'POST' });
  } catch {}
  window.location.href = '/login?locked=1';
}

function showToast(message, kind = 'ok', ms = 3800, action = null) {
  if (!toastStackEl) return () => {};
  const minByKind = {
    ok: 4000,
    warn: 5200,
    err: 6500,
  };
  const durationMs = Math.max(Number(ms) || 0, minByKind[kind] || 4000);

  const el = document.createElement('div');
  el.className = `toast ${kind}`;

  const textEl = document.createElement('div');
  textEl.textContent = message;
  el.appendChild(textEl);

  if (action?.label && typeof action?.onClick === 'function') {
    const btn = document.createElement('button');
    btn.className = 'toast-action';
    btn.textContent = action.label;
    btn.onclick = () => action.onClick();
    el.appendChild(btn);
  }

  toastStackEl.appendChild(el);

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    el.style.opacity = '0';
    el.style.transform = 'translateY(6px)';
    setTimeout(() => el.remove(), 180);
  };

  setTimeout(close, durationMs);
  return close;
}

async function api(path, options = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(path, { ...options, signal: controller.signal });
    if (!r.ok) {
      const txt = await r.text().catch(() => '');
      throw new Error(`HTTP ${r.status}${txt ? `: ${txt.slice(0, 160)}` : ''}`);
    }
    return r.json();
  } finally {
    clearTimeout(timer);
  }
}
