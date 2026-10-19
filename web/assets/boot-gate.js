/* The workspace page starts as <html class="booting">, which hides the workspace until
   app-boot.js has drawn the cached Tonies and tracks (no flash of empty panes). If a script
   fails before that, show the workspace anyway after a moment. */
setTimeout(() => document.documentElement.classList.remove('booting'), 1500);
