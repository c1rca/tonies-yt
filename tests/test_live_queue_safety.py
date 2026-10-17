"""Offline safety contracts for the explicitly opt-in disposable live probe."""
import copy
import importlib.util
from pathlib import Path

import pytest

from app.tonies_api import ToniesAPI
from tonies_fake import chapter


def probe_module():
    path = Path(__file__).parents[1] / 'scripts/live_queue_acceptance.py'
    assert path.is_file(), 'A recoverable opt-in live queue probe must exist'
    spec = importlib.util.spec_from_file_location('live_queue_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_opt_in_live_probe_is_available():
    probe_module()


@pytest.fixture
def probe(tmp_path):
    module = probe_module()
    api = module.ObservedAPI('synthetic@example.invalid', 'synthetic-only', tmp_path)
    api.baseline = {'chapters': [chapter('original')]}
    yield module, api
    api.close()


def test_original_chapter_change_or_removal_prevents_any_patch(probe, monkeypatch):
    _, api = probe
    calls = []
    monkeypatch.setattr(ToniesAPI, '_api', lambda *args, **kw: calls.append(args))
    changed = copy.deepcopy(api.baseline['chapters'])
    changed[0]['title'] = 'Corrupted'
    for chapters in [[], changed]:
        with pytest.raises(RuntimeError, match='preservation'):
            api._api('PATCH', '/synthetic', {'chapters': chapters})
    assert calls == []


def test_allocation_registered_before_storage_even_if_evidence_write_fails(probe, monkeypatch):
    module, api = probe
    monkeypatch.setattr(ToniesAPI, '_api', lambda *args, **kw: {'fileId': 'owned-staging'})
    monkeypatch.setattr(module, 'private_json', lambda *args: (_ for _ in ()).throw(OSError('synthetic')))
    api._api('POST', '/file', {'headers': {}})
    assert api.allocated == {'owned-staging'}
    assert api.capture_failures == 1


def test_original_or_repeated_allocation_refused(probe, monkeypatch):
    _, api = probe
    for identity in ['original', 'file-original', 'already-owned']:
        api.allocated.add('already-owned')
        monkeypatch.setattr(ToniesAPI, '_api', lambda *args, identity=identity, **kw: {'fileId': identity})
        with pytest.raises(RuntimeError, match='allocation'):
            api._api('POST', '/file', {})


def test_attachment_identity_retained_before_postwrite_verification(probe, monkeypatch):
    _, api = probe
    api.allocated.add('staging')
    desired = api.baseline['chapters'] + [chapter('staging', 'Owned unique title')]
    returned = api.baseline['chapters'] + [chapter('permanent', 'Owned unique title')]
    monkeypatch.setattr(ToniesAPI, '_api', lambda *args, **kw: {'chapters': returned})
    api._api('PATCH', '/synthetic', {'chapters': desired})
    assert api.owned == {'permanent'}
    assert api.mutations == [{'attachments': 1}]


def test_attachment_id_collision_with_original_file_reference_is_not_owned(probe, monkeypatch):
    _, api = probe
    api.allocated.add('staging')
    desired = api.baseline['chapters'] + [chapter('staging', 'Owned unique title')]
    returned = api.baseline['chapters'] + [chapter('file-original', 'Owned unique title')]
    monkeypatch.setattr(ToniesAPI, '_api', lambda *args, **kw: {'chapters': returned})
    api._api('PATCH', '/synthetic', {'chapters': desired})
    assert not api.owned, 'Original file references cannot become disposable chapter identities'



def test_read_contract_preserves_read_only_auth_retry_flag(probe, monkeypatch):
    _, api = probe
    seen = []
    monkeypatch.setattr(ToniesAPI, '_api', lambda *args, **kw: seen.append(kw) or {})
    api._api('POST', '/graphql', {'query': 'synthetic'}, read=True)
    assert seen == [{'read': True}]


@pytest.mark.parametrize('fail_readback', [False, True])
def test_full_probe_and_post_attachment_failure_both_restore_originals(tmp_path, monkeypatch, fail_readback):
    import httpx
    import json
    import threading
    from tonies_fake import Cloud

    module = probe_module()
    cloud = Cloud()
    original = copy.deepcopy(cloud.chapters)
    lock = threading.Lock()

    def transport(request):
        with lock:
            if request.url.path == '/v2/graphql':
                for item in cloud.chapters:
                    item['transcoding'] = False
            return cloud(request)

    real_init = module.ObservedAPI.__init__

    def init(api, email, password, evidence):
        real_init(api, email, password, evidence)
        api._client.close()
        api._client = httpx.Client(transport=httpx.MockTransport(transport))

    monkeypatch.setattr(module.ObservedAPI, '__init__', init)
    monkeypatch.setenv('TONIES_EMAIL', 'synthetic@example.invalid')
    monkeypatch.setenv('TONIES_PASSWORD', 'synthetic-only')
    if fail_readback:
        readback = module.ObservedAPI._readback
        failed = False

        def raise_after_attachment(api, *args, **kwargs):
            nonlocal failed
            if api.owned and not failed:
                failed = True
                raise RuntimeError('Synthetic post-attachment readback failure')
            return readback(api, *args, **kwargs)

        monkeypatch.setattr(module.ObservedAPI, '_readback', raise_after_attachment)
        with pytest.raises(SystemExit) as error:
            module.execute(tmp_path)
        assert error.value.code == 1
    else:
        module.execute(tmp_path)
    receipt = json.loads((tmp_path / 'receipt.json').read_text())
    assert receipt['ok'] is (not fail_readback)
    assert receipt['cleanup_exact'] is True
    assert receipt['owned_count'] == 3
    assert cloud.chapters == original
    assert not list(tmp_path.glob('Queue-Probe-*.mp3')), 'Remove all generated test audio after verified cleanup'

import json
import threading
import httpx
from tonies_fake import Cloud

def install_cloud(module, cloud, monkeypatch, mode):
    lock = threading.Lock()
    def transport(request):
        with lock:
            if request.url.path == '/v2/graphql':
                for item in cloud.chapters:
                    item['transcoding'] = ('error' if mode == 'processing_error' and
                                           item['id'].startswith('permanent') else False)
            response = cloud(request)
            if mode == 'config_changed' and request.url.path == '/v2/graphql' and len(cloud.patches) >= 3:
                body = response.json()
                body['data']['config']['maxChapters'] = 98
                return httpx.Response(200, json=body)
            return response
    real_init = module.ObservedAPI.__init__
    def init(api, email, password, evidence):
        real_init(api, email, password, evidence)
        api._client.close()
        api._client = httpx.Client(transport=httpx.MockTransport(transport))
    monkeypatch.setattr(module.ObservedAPI, '__init__', init)
    monkeypatch.setenv('TONIES_EMAIL', 'synthetic@example.invalid')
    monkeypatch.setenv('TONIES_PASSWORD', 'synthetic-only')


def test_processing_failure_still_removes_owned(tmp_path, monkeypatch):
    module, cloud = probe_module(), Cloud()
    originals = copy.deepcopy(cloud.chapters)
    install_cloud(module, cloud, monkeypatch, 'processing_error')
    with pytest.raises(SystemExit) as error:
        module.execute(tmp_path)
    assert error.value.code == 1
    receipt = json.loads((tmp_path / 'receipt.json').read_text())
    print('PROCESSING_FAULT', json.dumps({'receipt': receipt, 'patch_count': len(cloud.patches),
          'remaining_owned': [c['id'] for c in cloud.chapters if c['id'].startswith('permanent')]}))
    assert cloud.chapters[:len(originals)] == originals
    assert cloud.chapters == originals, 'Owned chapters remain solely because processing validation blocks cleanup reads'


def test_exact_restoration_reads_current_config(tmp_path, monkeypatch):
    module, cloud = probe_module(), Cloud()
    install_cloud(module, cloud, monkeypatch, 'config_changed')
    try:
        module.execute(tmp_path)
    except SystemExit as error:
        assert error.code == 1
    receipt = json.loads((tmp_path / 'receipt.json').read_text())
    print('CONFIG_FAULT', json.dumps({'receipt': receipt, 'patch_count': len(cloud.patches)}))
    assert receipt['cleanup_exact'] is False, 'Current config changed but cleanup compared copied baseline config'

