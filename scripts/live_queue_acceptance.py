#!/usr/bin/env python3
"""Opt-in three-clip live queue check; preserves every original chapter.

Run with TONIES_EMAIL/TONIES_PASSWORD in the environment, never command arguments:
  python scripts/live_queue_acceptance.py --execute --evidence-dir /private/new-run

Evidence contains account inventory: keep it outside Git. Never use this to test clear.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.actions import ActionQueue  # noqa: E402
from app.tonies_api import ToniesAPI, parse_target_url  # noqa: E402
from app.tonies_content import validated_chapters, writable_chapters  # noqa: E402


def private_json(path, value):
    data = json.dumps(value, indent=2, ensure_ascii=False)
    with path.open('x', encoding='utf-8') as stream:
        os.chmod(path, 0o600)
        stream.write(data)
    assert json.loads(path.read_text()) == value


class ObservedAPI(ToniesAPI):
    def __init__(self, email, password, evidence):
        super().__init__(email, password)
        self.evidence = evidence
        self.guard = threading.RLock()
        self.baseline: dict | None = None
        self.allocated = set()
        self.owned = set()
        self.mutations = []
        self.capture_failures = 0
        self.prepare_barrier = threading.Barrier(3, timeout=90)

    def capture(self, label, value):
        try:
            private_json(self.evidence / f'{label}-{uuid.uuid4().hex}.json', value)
        except Exception:
            # Evidence failures must never interrupt ownership registration or cleanup.
            self.capture_failures += 1

    def protect(self, chapters):
        assert self.baseline is not None, 'A verified baseline is required before writes'
        originals = self.baseline['chapters']
        original_ids = {c['id'] for c in originals}
        kept = [c for c in chapters if c['id'] in original_ids]
        if writable_chapters(kept) != writable_chapters(originals):
            raise RuntimeError('Original chapter preservation check failed; refusing mutation')

    def _api(self, method: str, path: str, payload: dict, *, read=False) -> dict:
        if method == 'PATCH':
            with self.guard:
                self.protect(payload['chapters'])
        result = super()._api(method, path, payload, read=read)
        with self.guard:
            if method == 'POST' and path == '/file':
                identity = result.get('fileId')
                original_refs = {c[k] for c in self.baseline['chapters'] for k in ('id', 'file')}
                if not isinstance(identity, str) or not identity or identity in original_refs or identity in self.allocated:
                    raise RuntimeError('Unsafe allocation identity')
                self.allocated.add(identity)  # before any storage transfer can start
                self.capture('allocation', {'file_id': identity})
            elif method == 'PATCH':
                desired, returned = payload['chapters'], result.get('chapters', [])
                original_refs = {c[k] for c in self.baseline['chapters'] for k in ('id', 'file')}
                if len(desired) == len(returned):
                    for want, got in zip(desired, returned):
                        if (want['id'] in self.allocated and got.get('id') not in original_refs
                                and got.get('title') == want['title'] and got.get('type') == 'file'):
                            self.owned.add(got['id'])  # retain attachment identity before readback
                count = sum(c['id'] in self.allocated for c in desired)
                self.mutations.append({'attachments': count})
                self.capture('attachment', {'owned_ids': sorted(self.owned), 'returned': returned})
        return result

    def prepare_upload(self, path, target_url):
        result = super().prepare_upload(path, target_url)
        self.prepare_barrier.wait()  # all three transfers finish before batching attachments
        return result

    def recovery_snapshot(self, ids):
        """Inspect failed processing only on already-owned IDs; never treat it as success."""
        data = self._query(ids)
        matches = [target for house in data['households'] if house['id'] == ids[0]
                   for target in house['creativeTonies'] if target['id'] == ids[1]]
        if len(matches) != 1:
            raise RuntimeError('Recovery target is ambiguous')
        target = matches[0]
        checked = copy.deepcopy(target['chapters'])
        for chapter in checked:
            if chapter.get('id') in self.owned:
                chapter['transcoding'] = False
        validated_chapters(checked)  # unknown/original processing failures still refuse recovery
        self.protect(target['chapters'])
        return target, data['config']

    def remove_owned(self, url):
        ids = parse_target_url(url)
        with self._target_lock(ids):
            current, _ = self.recovery_snapshot(ids)
            desired = [c for c in current['chapters'] if c['id'] not in self.owned]
            if len(desired) == len(current['chapters']):
                return
            fresh, _ = self.recovery_snapshot(ids)
            if writable_chapters(fresh['chapters']) != writable_chapters(current['chapters']):
                raise RuntimeError('Recovery inventory changed; refusing stale write')
            self._api('PATCH', f'/households/{ids[0]}/creativetonies/{ids[1]}',
                      {'chapters': writable_chapters(desired)})
            confirmed, _ = self.recovery_snapshot(ids)
            if writable_chapters(confirmed['chapters']) != writable_chapters(desired):
                raise RuntimeError('Owned removal was not confirmed; do not retry blindly')


def wait_actions(queue, actions, timeout=120):
    deadline = time.monotonic() + timeout
    for action in actions:
        queue.wait(action.id, max(0, deadline - time.monotonic()))
    if any(not a.finished.is_set() for a in actions):
        raise RuntimeError('Queue did not settle; do not repeat the upload')
    if any(a.status != 'done' for a in actions):
        raise RuntimeError('One or more actions failed; cleanup will use recorded identities')


def wait_ready(api, url, ids, deadline_seconds=180):
    deadline = time.monotonic() + deadline_seconds
    while True:
        target, config = api._snapshot(ids)
        api.protect(target['chapters'])
        content = api.get_content(url)
        if not content['processing']:
            return target, config
        if time.monotonic() >= deadline:
            raise RuntimeError('Processing deadline exceeded')
        time.sleep(1)


def execute(evidence):
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    api = ObservedAPI(os.environ['TONIES_EMAIL'], os.environ['TONIES_PASSWORD'], evidence)
    uploads = []
    media = []
    selected_url = None
    all_before = []
    failure = None
    cleanup_ok = False
    queue = None
    try:
        targets = api.list_targets()
        for target in targets:
            raw, config = api._snapshot(parse_target_url(target['edit_url']))
            all_before.append({'url': target['edit_url'], 'target': raw, 'config': config})
        private_json(evidence / 'before.json', all_before)  # mandatory verified baseline before writes
        eligible = [t for t in all_before if not t['target'].get('transcoding')
                    and not any(c.get('transcoding') for c in t['target']['chapters'])
                    and t['target']['secondsRemaining'] >= 15
                    and len(t['target']['chapters']) + 3 <= t['config']['maxChapters']]
        if not eligible:
            raise RuntimeError('No safe target with enough capacity')
        chosen = max(eligible, key=lambda t: t['target']['secondsRemaining'])
        selected_url, api.baseline = chosen['url'], copy.deepcopy(chosen['target'])
        ids = parse_target_url(selected_url)
        prefix = 'Queue-Probe-' + uuid.uuid4().hex[:12]
        queue = ActionQueue(lambda: api, batch_window=0.5)
        for i in range(3):
            path = evidence / f'{prefix}-{i + 1}.mp3'
            media.append(path)
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                            f'sine=frequency={400 + i * 100}:duration=2', '-ac', '2', '-ar', '44100',
                            '-codec:a', 'libmp3lame', '-b:a', '128k', str(path)],
                           check=True, capture_output=True, timeout=30)
            uploads.append(queue.submit_upload(selected_url, path, path.stem, {'edge': 'end'}))
        wait_actions(queue, uploads)
        owned_order = [a.chapter_id for a in uploads]
        assert len(set(owned_order)) == 3 and all(c in api.owned for c in owned_order)
        assert any(m['attachments'] == 3 for m in api.mutations), 'Three-way attachment was not exercised'
        current, _ = wait_ready(api, selected_url, ids)
        assert [c['id'] for c in current['chapters'] if c['id'] in api.owned] == owned_order
        edits = queue.submit(selected_url, [
            {'type': 'rename', 'chapter_id': owned_order[0], 'title': prefix + ' Renamed café'},
            {'type': 'move', 'chapter_id': owned_order[2], 'position': {'before': owned_order[0]}},
        ])
        wait_actions(queue, edits)
        current, _ = api._snapshot(ids)
        api.protect(current['chapters'])
        assert [c['id'] for c in current['chapters'] if c['id'] in api.owned] == [owned_order[2], owned_order[0], owned_order[1]]
        assert next(c for c in current['chapters'] if c['id'] == owned_order[0])['title'] == prefix + ' Renamed café'
        removals = queue.submit(selected_url, [{'type': 'delete', 'chapter_id': c} for c in owned_order])
        wait_actions(queue, removals)
    except Exception as error:
        failure = type(error).__name__  # never print private server payloads or signed URLs
    finally:
        try:
            if selected_url:
                # Wait for transfers/writes, never race cleanup with a still-running mutation.
                if uploads:
                    wait_actions_settled = all(a.finished.wait(180) for a in uploads)
                    if not wait_actions_settled:
                        raise RuntimeError('Uploads still in flight; private recovery evidence retained')
                api.remove_owned(selected_url)
            after = []
            for item in all_before:
                target, config = api._snapshot(parse_target_url(item['url']))
                after.append({'url': item['url'], 'target': target, 'config': config})
            assert after == all_before, 'Exact original target/capacity restoration failed'
            private_json(evidence / 'after.json', after)
            for path in media:
                path.unlink(missing_ok=True)
            cleanup_ok = True
        except Exception as cleanup_error:
            api.capture('cleanup-failure', {'kind': type(cleanup_error).__name__, 'owned_ids': sorted(api.owned)})
        receipt = {'ok': failure is None and cleanup_ok and api.capture_failures == 0,
                   'failure_kind': failure, 'cleanup_exact': cleanup_ok, 'capture_failures': api.capture_failures,
                   'upload_count': len(uploads), 'owned_count': len(api.owned), 'mutations': api.mutations}
        private_json(evidence / 'receipt.json', receipt)
        api.close()
        print(json.dumps(receipt))
        if not receipt['ok']:
            raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Authorize only the three disposable clips')
    parser.add_argument('--evidence-dir', required=True, type=Path)
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute is required for live writes')
    args.evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    execute(args.evidence_dir)
