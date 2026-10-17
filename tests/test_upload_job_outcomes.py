"""Queue cancellation and inverse outcomes must not resurrect upload jobs."""
import copy

import pytest

from app import jobs
from app.actions import Action


@pytest.fixture
def upload(monkeypatch):
    monkeypatch.setattr(jobs, "_jobs", {})
    monkeypatch.setattr(jobs, "_persist_jobs_state", lambda: None)
    monkeypatch.setattr(jobs, "_emit_event", lambda *_args, **_kwargs: None)
    state = jobs.create_job("synthetic local upload")
    jobs._update(state.id, status="uploading", output_file="synthetic-local.mp3", tonies_action_id="action-one")
    action = Action("action-one", 1, "synthetic-target", "upload", {"position": {"edge": "start"}})
    return state, action


def complete(state, action):
    action.status = "done"
    action.chapter_id = "permanent-upload"
    action.content = {"chapters": [{"chapter_id": "permanent-upload", "title": "Synthetic"}], "processing": False}
    jobs._on_upload_action(state.id, action)
    assert state.status == "done"
    assert state.uploaded_chapter_id == "permanent-upload"


def test_canceled_queue_upload_terminates_job_without_failure_or_receipt(upload):
    state, action = upload
    action.status = "canceled"
    jobs._on_upload_action(state.id, action)
    assert state.status == "canceled"
    assert state.cancel_requested is True
    assert state.error is None
    assert state.uploaded_chapter_id is None and state.tonies_content is None
    action.status, action.error = "failed", "late transfer failure"
    jobs._on_upload_action(state.id, action)
    assert state.status == "canceled" and state.error is None


def test_confirmed_upload_undo_clears_historical_attachment_from_job(upload):
    state, action = upload
    complete(state, action)
    old_content = copy.deepcopy(action.content)
    action.status, action.content = "undone", {"chapters": [], "processing": False}
    jobs._on_upload_action(state.id, action)
    assert state.status == "undone" and state.error is None
    assert state.output_file == "synthetic-local.mp3"
    assert state.uploaded_chapter_id is None and state.tonies_content is None
    assert state.upload_positioned is False
    # A delayed original callback must not claim the reversed upload is saved.
    action.status, action.content = "done", old_content
    jobs._on_upload_action(state.id, action)
    assert state.status == "undone" and state.tonies_content is None


def test_unconfirmed_inverse_retains_forward_identity_without_success_claim(upload):
    state, action = upload
    complete(state, action)
    action.content = None
    action.needs_refresh = True
    action.error = "Undo unavailable or outcome unconfirmed; refresh the Tonie before another action"
    jobs._on_upload_action(state.id, action)
    assert state.status == "done"  # Forward completion is still a historical fact.
    assert state.uploaded_chapter_id == "permanent-upload"
    assert state.error == action.error and state.needs_refresh is True
    # The stored old receipt is historical, not a new canonical inverse result.
    assert state.tonies_content["chapters"][0]["chapter_id"] == "permanent-upload"


@pytest.mark.parametrize("status", ["canceled", "undone"])
def test_cancel_api_does_not_reissue_cancellation_for_terminal_upload(upload, monkeypatch, status):
    state, action = upload
    jobs._update(state.id, status=status)
    monkeypatch.setattr(jobs.action_queue, "cancel", lambda *_: pytest.fail("terminal job must not replay cancellation"))
    assert jobs.cancel_job(state.id).status == status
