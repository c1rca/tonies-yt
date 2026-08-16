from pathlib import Path
import subprocess
import sys

from app import uploader


def test_upload_from_running_event_loop_does_not_deadlock():
    code = """
import asyncio
from pathlib import Path
from app import uploader
uploader._resolved_tonies_auth = lambda: ('', '', '')
async def main():
    try:
        uploader.upload_to_tonies(Path('/tmp/missing.mp3'))
    except RuntimeError:
        print('completed')
asyncio.run(main())
"""
    proc = subprocess.run(
        [sys.executable, '-c', code],
        cwd=Path(__file__).parents[1],
        env={**__import__('os').environ, 'PYTHONPATH': '.'},
        capture_output=True,
        text=True,
        timeout=2,
    )
    assert proc.returncode == 0
    assert 'completed' in proc.stdout


def test_sign_in_shell_is_clicked_before_credential_fields_are_expected():
    clicks = []

    class Locator:
        @property
        def first(self):
            return self

        def count(self):
            return 1

        def is_visible(self, timeout=None):
            return True

        def click(self):
            clicks.append('sign-in')

    class Page:
        def locator(self, selector):
            assert 'SIGN IN' in selector
            return Locator()

    assert uploader._click_tonies_sign_in_shell(Page()) is True
    assert clicks == ['sign-in']


def test_extract_tonies_content_rejects_non_editor_empty_page():
    class Body:
        def inner_text(self, timeout=None):
            return "SIGN IN"

    class Page:
        def locator(self, selector):
            assert selector == "body"
            return Body()

        def evaluate(self, _script):
            return []

    try:
        uploader._extract_tonies_content(Page())
    except RuntimeError as exc:
        assert "editor content was not available" in str(exc)
    else:
        raise AssertionError("login/transient pages must not be returned as an empty Tonies")


def test_upload_confirmation_requires_the_unique_track_token():
    before_duplicate = "Sesame Street Elmo Song [oc:aaaaaa]"
    assert not uploader._titles_contain_track_token([before_duplicate], "bbbbbb")
    assert uploader._titles_contain_track_token(
        [before_duplicate, "Sesame Street Elmo Song [oc:bbbbbb]"],
        "bbbbbb",
    )


def test_rename_preserves_existing_app_track_token():
    assert uploader._renamed_title(
        "Blaze Song [oc:6abbd9]",
        "Blaze QA Rename",
    ) == "Blaze QA Rename [oc:6abbd9]"


def test_upload_does_not_skip_save_for_unrelated_finished_text():
    source = (Path(uploader.__file__)).read_text(encoding="utf-8")
    assert "if (not saw_success_signal) and save_btn is not None:" not in source


def test_move_list_item_uses_insertion_semantics():
    assert uploader._move_list_item(["a", "b", "c", "d"], 1, 3) == ["a", "c", "d", "b"]
    assert uploader._move_list_item(["a", "b", "c", "d"], 3, 1) == ["a", "d", "b", "c"]


def test_remove_list_item_keeps_the_exact_remaining_order():
    assert uploader._remove_list_item(["a", "b", "c"], 1) == ["a", "c"]


def test_new_upload_row_is_the_first_row_after_the_pre_upload_count():
    assert uploader._new_upload_row_index(6, 7) == 6


def test_upload_persistence_accepts_a_new_remote_chapter_when_tonies_strips_filename_token():
    assert uploader._upload_persisted(
        before_count=6,
        current_titles=["one", "two", "three", "four", "five", "six", "Dora Pirate Adventure"],
        token="visualdora1",
    )


def test_transient_editor_content_error_is_retryable():
    assert uploader._is_transient_editor_error(
        RuntimeError("Tonies editor content was not available (login or transient page)")
    )


def test_mutation_editor_retry_reopens_after_a_transient_empty_page(monkeypatch):
    opens = []
    results = [RuntimeError("Tonies editor content was not available (login or transient page)"), {"chapters": []}]

    class Page:
        def wait_for_timeout(self, ms):
            assert ms == 2000

    monkeypatch.setattr(uploader, "_open_tonies_editor", lambda page, url: opens.append(url))
    monkeypatch.setattr(uploader, "_extract_tonies_content", lambda page: (_ for _ in ()).throw(results.pop(0)) if isinstance(results[0], Exception) else results.pop(0))

    assert uploader._open_tonies_editor_with_retry(Page(), "https://example.test/editor") == {"chapters": []}
    assert opens == ["https://example.test/editor", "https://example.test/editor"]


def test_mutations_validate_and_reopen_a_transient_editor_before_acting():
    source = Path(uploader.__file__).read_text(encoding="utf-8")
    for name in [
        "delete_tonies_chapter",
        "delete_all_tonies_content",
        "rename_tonies_chapter",
        "reorder_tonies_chapter",
    ]:
        operation = source[source.index(f"def {name}"):]
        assert "_open_tonies_editor_with_retry(page, target_url)" in operation


def test_refresh_page_is_polled_until_editor_is_ready():
    class Page:
        url = "https://my.tonies.com/refresh?relatedUrl=x"
        reloads = 0

        def reload(self, wait_until=None, timeout=None):
            self.reloads += 1
            if self.reloads == 2:
                self.url = "https://my.tonies.com/creative-tonies/x/edit"

        def wait_for_timeout(self, timeout):
            assert timeout > 0

    page = Page()
    assert uploader._wait_for_tonies_refresh(page, 5000) is True
    assert page.reloads == 2
    assert "/refresh" not in page.url


def test_creative_tonies_listing_does_not_wait_for_network_idle():
    source = Path(uploader.__file__).read_text(encoding="utf-8")
    listing = source[source.index("def list_creative_tonies"):]
    assert 'wait_for_load_state("networkidle")' not in listing
    retry = listing[listing.index("page.reload(wait_until=\"domcontentloaded\")"):]
    assert 'page.wait_for_selector("a[href*=\'/creative-tonies/\']", timeout=10000)' in retry


def test_sso_sign_in_handoff_revisits_requested_page_after_callback():
    class Locator:
        def count(self):
            return 0

    class Page:
        url = "https://my.tonies.com/login#state=callback"
        visits = []

        def wait_for_timeout(self, timeout):
            assert timeout == 8000

        def locator(self, selector):
            return Locator()

        def goto(self, url, wait_until=None):
            self.visits.append(url)
            self.url = url

    page = Page()
    target = "https://my.tonies.com/creative-tonies"
    assert uploader._finish_tonies_sign_in_handoff(page, target) is True
    assert page.visits == [target]


def test_drag_adjacent_does_not_pre_scroll_dynamic_nth_locators():
    calls = []

    class FakeLocator:
        def __init__(self, index):
            self.index = index

        def scroll_into_view_if_needed(self, **_kwargs):
            raise AssertionError("pre-scrolling can rebind virtualized nth locators")

        def drag_to(self, other):
            calls.append((self.index, other.index))

    class FakeRows:
        def nth(self, index):
            return FakeLocator(index)

    class FakePage:
        def locator(self, _selector):
            return FakeRows()

    uploader._drag_adjacent(FakePage(), ".row", 2, 3)
    assert calls == [(2, 3)]


def test_wait_for_tonies_order_reloads_until_saved_order_is_visible(monkeypatch):
    expected = ["a", "c", "b"]
    seen = iter([[], ["a", "b", "c"], expected])

    class FakePage:
        def __init__(self):
            self.visits = []
            self.waits = []

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

        def goto(self, url, wait_until=None):
            self.visits.append(url)

    page = FakePage()
    monkeypatch.setattr(uploader, "_extract_chapter_titles", lambda _page: next(seen))
    monkeypatch.setattr(uploader, "_extract_tonies_content", lambda _page: {"chapters": expected})
    monkeypatch.setattr(uploader, "_wait_for_tonies_editor_ready", lambda *_args, **_kwargs: None)

    result = uploader._wait_for_tonies_order(page, "https://example/edit", expected, timeout_ms=5000)

    assert result == {"chapters": expected}
    assert page.visits == ["https://example/edit", "https://example/edit"]


def test_wait_for_tonies_order_rejects_wrong_settled_order(monkeypatch):
    class FakePage:
        def wait_for_timeout(self, _milliseconds):
            pass

        def goto(self, _url, wait_until=None):
            pass

    monkeypatch.setattr(uploader, "_extract_chapter_titles", lambda _page: ["wrong"])
    monkeypatch.setattr(uploader, "_wait_for_tonies_editor_ready", lambda *_args, **_kwargs: None)
    times = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(uploader.time, "time", lambda: next(times, 2.0))

    try:
        uploader._wait_for_tonies_order(FakePage(), "https://example/edit", ["expected"], timeout_ms=1000)
    except RuntimeError as exc:
        assert "Saved Tonies order was not confirmed" in str(exc)
    else:
        raise AssertionError("wrong order must not be reported as success")
