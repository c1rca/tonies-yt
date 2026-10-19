/** Short checks for editor downloads that can outlive a reverse-proxy request. */
async function fetchEditorCandidate(jobId, index, session) {
  const deadline = Date.now() + 900000;
  let selection = {index};
  while (Date.now() < deadline) {
    ensureEditorSession(session);
    const result = await api(`/api/jobs/${encodeURIComponent(jobId)}/fetch`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(selection)
    }, 15000);
    ensureEditorSession(session);
    if (result.filename) return result.filename;
    if (result.status !== 'preparing') throw Error('Could not prepare this video; try again.');
    selection = {index, poll: true};
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw Error('This video is taking too long to prepare. Check Local files before trying again.');
}
