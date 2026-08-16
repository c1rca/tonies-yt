from pathlib import Path


ROOT = Path(__file__).parents[1]


def _body(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_upload_worker_does_not_block_completion_on_eventual_remote_listing():
    jobs = (ROOT / "app" / "jobs.py").read_text()
    worker = _body(jobs, "def upload_worker_loop", "def select_candidate_and_continue")
    assert "verify_strict=False" in worker


def test_completed_upload_is_immediate_and_remote_confirmation_is_backgrounded():
    html = (ROOT / "web" / "index.html").read_text()
    finalizer = _body(html, "async function finalizePendingToniesUpload", "function syncPendingToniesUploadFromJob")
    assert "addOptimisticUploadedChapter(completedFilename, targetUrl)" in finalizer
    assert "void refreshToniesContentWithFollowups(targetUrl" in finalizer
    assert "await Promise.race" not in finalizer
    assert "function mergeOptimisticUploadedChapters" in html


def test_drop_order_is_not_applied_until_the_remote_uploaded_row_is_present():
    html = (ROOT / "web" / "index.html").read_text()
    refresh = _body(html, "async function refreshToniesContent(options", "function getToniesIdentityKey")
    drop = _body(refresh, "if (pendingDropUploadIntent", "pendingDropUploadIntent = null")
    assert "if (newIdx < 0)" in drop
    assert "scheduleToniesContentRetry" in drop
    assert "pendingDropUploadIntent = null" not in drop
