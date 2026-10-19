/* app-boot.js reveals the workspace after restoring its cached view. If a script
   fails, DOMContentLoaded still fires: fail open then, not while slow scripts
   are pending and the workspace is still an empty template. */
document.addEventListener('DOMContentLoaded', () => {
  document.documentElement.classList.remove('booting');
}, {once:true});
