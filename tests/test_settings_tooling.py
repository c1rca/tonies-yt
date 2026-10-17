"""Exercise the actual settings script without network or package installs."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("installed", ["2026.01.01", "unknown"])
def test_settings_reports_tools_without_offering_runtime_install(installed):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for settings JavaScript behavior")
    assert node is not None
    html = (ROOT / "web/account.html").read_text()
    guidance = re.search(r'<div id="status-ytdlp"[^>]*>([^<]+)</div>', html)
    assert guidance is not None and "rebuild" in guidance.group(1).lower()
    assert not re.search(r'<button\b[^>]*\bid=[\"\']update-ytdlp[\"\']', html)
    script = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.DOTALL)[-1]
    probe = r'''
const assert = require('node:assert/strict');
const elements = new Map();
const calls = [];
global.document = {getElementById(id) {
  if (!elements.has(id)) elements.set(id, {textContent: '', value: '', style: {}});
  return elements.get(id);
}};
global.document.getElementById('status-ytdlp').textContent = GUIDANCE;
global.localStorage = {getItem() {return null;}};
global.window = {location: {href: ''}};
global.fetch = async (url, options) => {
  calls.push({url, method: options?.method || 'GET'});
  assert.equal(url, '/api/setup/status');
  return {json: async () => ({configured: true, unlocked: true, username: 'synthetic',
    app_version: '2.0.0', versions: {
      yt_dlp: INSTALLED, deno: 'deno synthetic'
    }})};
};
(async () => {
  SCRIPT
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(document.getElementById('ver-ytdlp').textContent, INSTALLED);
  assert.equal(document.getElementById('ver-deno').textContent, 'deno synthetic');
  assert.equal(document.getElementById('ver-app').textContent, '2.0.0');
  assert.match(document.getElementById('status-ytdlp').textContent, /rebuild/i);
  assert.equal(elements.has('update-ytdlp'), false);
  assert.equal(elements.has('ytdlp-current-row'), false);
  assert.deepEqual(calls, [{url: '/api/setup/status', method: 'GET'}]);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    probe = probe.replace("INSTALLED", json.dumps(installed)).replace("GUIDANCE", json.dumps(guidance.group(1))).replace("SCRIPT", script)
    result = subprocess.run([node, "-e", probe], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "/api/setup/update-yt-dlp" not in html
    assert "yt-dlp is up to date" not in html
