/** Single native local preview. Authenticated media URL; no remote Tonie playback. */
function updateLocalPlaybackButtons() {
  filesEl.querySelectorAll('.file').forEach((row) => {
    const name = row.getAttribute('data-file-name') || '';
    const button = row.querySelector('.local-play-btn');
    if (!button) return;
    const playing = name === localPlaybackFilename && !localAudioEl.paused;
    button.textContent = playing ? 'Pause' : 'Play';
    button.setAttribute('aria-label', `${playing ? 'Pause' : 'Play'} ${name}`);
  });
}

function stopLocalPlayback() {
  localPlaybackIntent += 1;
  localPlaybackFilename = '';
  localAudioEl.pause();
  localAudioEl.removeAttribute('src');
  localAudioEl.load();
  localPlayerEl.hidden = true;
  localPlayerNameEl.textContent = '';
  localPlayerStatusEl.textContent = '';
  updateLocalPlaybackButtons();
}

async function toggleLocalPlayback(filename) {
  if (!filesCache.some(f => f.name === filename)) return;
  const intent = ++localPlaybackIntent;
  localPlayerStatusEl.textContent = '';
  if (filename === localPlaybackFilename && !localAudioEl.paused) {
    localAudioEl.pause();
    return;
  }
  if (filename !== localPlaybackFilename) {
    localAudioEl.pause();
    localPlaybackFilename = filename;
    localPlayerNameEl.textContent = filename;
    localAudioEl.src = `/api/files/${encodeURIComponent(filename)}?inline=true`;
    localAudioEl.load();
  }
  localPlayerEl.hidden = false;
  try {
    await localAudioEl.play();
  } catch {
    // A source switch, pause or close can reject an earlier play request.
    if (intent === localPlaybackIntent) {
      localPlayerStatusEl.textContent = 'Could not play this file. Try again or refresh the library.';
    }
  }
  updateLocalPlaybackButtons();
}

