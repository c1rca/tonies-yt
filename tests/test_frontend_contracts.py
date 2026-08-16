from pathlib import Path


def test_reorder_waits_for_server_side_playwright_verification():
    html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    start = html.index("async function reorderToniesChapter")
    end = html.index("async function deleteFile", start)
    reorder = html[start:end]

    assert "body: JSON.stringify({ target_url: selectedToniesUrl, from_index: requestFromIndex, to_index: requestToIndex })\n          }, 60000);" in reorder


def test_second_candidate_click_is_ignored_while_selection_is_pending():
    html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    start = html.index("async function pickCandidate")
    end = html.index("function focusUploadInLibrary", start)
    pick = html[start:end]
    assert "queueCandidateSelection" not in pick
    assert "takeQueuedCandidateSelection" not in pick


def test_local_file_actions_do_not_embed_filename_in_inline_javascript():
    html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    start = html.index("function renderFiles()")
    end = html.index("function getResultsRenderState", start)
    render = html[start:end]
    assert "onclick=\"uploadExisting('" not in render
    assert "onclick=\"deleteFile('" not in render


def test_tonies_mutations_do_not_automatically_retry_non_idempotent_requests():
    html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    scopes = [
        ("async function saveToniesNameEdits", "function renderToniesContent"),
        ("async function deleteToniesChapter", "async function reorderToniesChapter"),
        ("async function reorderToniesChapter", "async function deleteFile"),
    ]
    for start_marker, end_marker in scopes:
        start = html.index(start_marker)
        end = html.index(end_marker, start)
        body = html[start:end]
        function_name = start_marker.split()[-1]
        assert body.count(f"{function_name}(") == 1


def test_rename_uses_mutation_sized_timeout():
    html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    start = html.index("async function saveToniesNameEdits")
    end = html.index("function renderToniesContent", start)
    rename = html[start:end]
    assert "}, 60000);" in rename


def test_completed_upload_sync_has_a_bounded_ui_watchdog():
    html = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    start = html.index("function startPendingToniesUpload")
    end = html.index("function clearPendingToniesUpload", start)
    body = html[start:end]
    assert "}, 180000);" in body
    assert "Upload still processing on my.tonies.com" in body
    assert "toniesUploadSyncing = false" in body
