/* Apply the saved appearance before the first paint, on every page (same key as the workspace). */
(() => {
  let theme = 'system';
  try { theme = localStorage.getItem('tonies-auto.workspace-theme.v1') || theme; } catch {}
  document.documentElement.setAttribute('data-theme', ['light', 'dark'].includes(theme) ? theme : 'system');
})();
