/** One confirmation at a time, initial focus on the safe action, bounded keyboard focus. */
const confirmModalEl = document.getElementById('confirm-modal');
const confirmTitleEl = document.getElementById('confirm-title');
const confirmMessageEl = document.getElementById('confirm-message');
const confirmOkEl = document.getElementById('confirm-ok');
const confirmCancelEl = document.getElementById('confirm-cancel');
let workspaceConfirmationOpen = false;
function askConfirm({title = 'Confirm action', message = '', confirmText = 'Confirm'} = {}) {
  if (workspaceConfirmationOpen) return Promise.resolve(false);
  workspaceConfirmationOpen = true;
  const launcher = document.activeElement;
  const priorKeydown = document.onkeydown;
  return new Promise(resolve => {
    confirmTitleEl.textContent = title;
    confirmMessageEl.textContent = message;
    confirmOkEl.textContent = confirmText;
    let settled = false;
    const close = result => {
      if (settled) return;
      settled = true;
      workspaceConfirmationOpen = false;
      confirmModalEl.classList.remove('open');
      confirmModalEl.setAttribute('aria-hidden', 'true');
      confirmOkEl.onclick = null;
      confirmCancelEl.onclick = null;
      confirmModalEl.onclick = null;
      document.onkeydown = priorKeydown;
      launcher?.focus();
      resolve(result);
    };
    confirmOkEl.onclick = () => close(true);
    confirmCancelEl.onclick = () => close(false);
    confirmModalEl.onclick = event => { if (event.target === confirmModalEl) close(false); };
    document.onkeydown = event => {
      if (event.key === 'Escape') close(false);
      if (event.key === 'Tab') {
        event.preventDefault();
        (document.activeElement === confirmCancelEl ? confirmOkEl : confirmCancelEl).focus();
      }
    };
    confirmModalEl.classList.add('open');
    confirmModalEl.setAttribute('aria-hidden', 'false');
    confirmCancelEl.focus();
  });
}
