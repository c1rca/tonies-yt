from pathlib import Path
from unittest.mock import Mock

import pytest

from app import jobs


def run_one_upload(monkeypatch, item):
    queue = Mock()
    queue.get.side_effect = [item, StopIteration]
    monkeypatch.setattr(jobs, '_upload_q', queue)
    # Protect the old implementation from ever spawning a real cloud worker.
    if hasattr(jobs, '_upload_once_with_timeout'):
        monkeypatch.setattr(jobs, '_upload_once_with_timeout', Mock(return_value=None))
    with pytest.raises(StopIteration):
        jobs.upload_worker_loop()
    queue.task_done.assert_called_once()


def make_upload(tmp_path):
    path = tmp_path / 'track.mp3'
    path.write_bytes(b'synthetic-audio')
    state = jobs.create_job('synthetic direct upload')
    jobs._update(state.id, status='waiting_upload')
    return state, {'job_id': state.id, 'mp3_path': str(path), 'target': None, 'target_url': 'synthetic-target'}


def test_direct_upload_uses_one_call_and_publishes_authoritative_result(monkeypatch, tmp_path):
    state, item = make_upload(tmp_path)
    content = {'chapters': [{'chapter_id': 'server-assigned', 'title': 'Synthetic'}], 'processing': True}
    upload = Mock(return_value={'chapter_id': 'server-assigned', 'target_url': 'synthetic-target', 'content': content})
    monkeypatch.setattr(jobs, 'upload_to_tonies', upload)
    run_one_upload(monkeypatch, item)
    upload.assert_called_once()
    result = jobs.get_job(state.id)
    assert result.status == 'done'
    assert result.uploaded_chapter_id == 'server-assigned'
    assert result.tonies_content == content
    assert result.target_url == 'synthetic-target'


def test_unconfirmed_upload_is_not_retried_with_original_file(monkeypatch, tmp_path):
    state, item = make_upload(tmp_path)
    item['fallback_path'] = item['mp3_path']
    upload = Mock(side_effect=RuntimeError('Upload delivery could not be confirmed; refresh before retry'))
    monkeypatch.setattr(jobs, 'upload_to_tonies', upload)
    run_one_upload(monkeypatch, item)
    upload.assert_called_once()
    assert jobs.get_job(state.id).status == 'failed'
    assert 'could not be confirmed' in jobs.get_job(state.id).error


def test_missing_upload_receipt_cannot_mark_job_done(monkeypatch, tmp_path):
    state, item = make_upload(tmp_path)
    monkeypatch.setattr(jobs, 'upload_to_tonies', Mock(return_value=None))
    run_one_upload(monkeypatch, item)
    assert jobs.get_job(state.id).status == 'failed'
    assert 'confirmed' in jobs.get_job(state.id).error.lower()


def test_receipt_must_contain_the_exact_uploaded_chapter(monkeypatch, tmp_path):
    state, item = make_upload(tmp_path)
    monkeypatch.setattr(jobs, 'upload_to_tonies', Mock(return_value={'chapter_id': 'expected', 'content': {'chapters': [{'chapter_id': 'other'}]}}))
    run_one_upload(monkeypatch, item)
    assert jobs.get_job(state.id).status == 'failed'


def test_cancelled_upload_does_not_reach_cloud(monkeypatch, tmp_path):
    state, item = make_upload(tmp_path)
    jobs.cancel_job(state.id)
    upload = Mock()
    monkeypatch.setattr(jobs, 'upload_to_tonies', upload)
    run_one_upload(monkeypatch, item)
    upload.assert_not_called()
    assert jobs.get_job(state.id).status == 'failed'


def test_queue_does_not_copy_credentials(monkeypatch, tmp_path):
    state, item = make_upload(tmp_path)
    monkeypatch.setattr(jobs, 'get_credentials', Mock(side_effect=AssertionError('Do not capture stale credentials in queue'))) if hasattr(jobs, 'get_credentials') else None
    queue = Mock()
    monkeypatch.setattr(jobs, '_upload_q', queue)
    jobs._enqueue_upload(state.id, Path(item['mp3_path']), target=None, target_url='synthetic-target')
    payload = queue.put.call_args.args[0]
    assert 'tonies_email' not in payload
    assert 'tonies_password' not in payload
    assert 'fallback_path' not in payload


def test_capacity_uses_exact_seconds_not_rounded_display_minutes(monkeypatch):
    monkeypatch.setattr(jobs, 'get_tonies_content', lambda _: {'free_seconds': 61.5, 'free_minutes': 1})
    jobs._ensure_tonies_capacity('synthetic-target', 61.2)
    with pytest.raises(RuntimeError, match='Not enough free'):
        jobs._ensure_tonies_capacity('synthetic-target', 63)


def test_unknown_capacity_fails_closed(monkeypatch):
    monkeypatch.setattr(jobs, 'get_tonies_content', lambda _: {'free_minutes': None})
    with pytest.raises(RuntimeError, match='capacity|space|free'):
        jobs._ensure_tonies_capacity('synthetic-target', 10)
