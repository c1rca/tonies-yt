from pathlib import Path
from datetime import datetime
from threading import Lock, Thread
from uuid import uuid4
import re
from .config import settings
from .credentials import get_credentials
from .logger import get_logger
import time

logger = get_logger(__name__)

_APP_TRACK_TOKEN_RE = re.compile(r"\s\[(?:oc:)?([a-f0-9]{4,12})\]$", re.IGNORECASE)


def _extract_app_track_token(value: str) -> str:
    m = _APP_TRACK_TOKEN_RE.search(str(value or '').strip())
    return (m.group(1).lower() if m else '')


def _strip_app_track_token(value: str) -> str:
    raw = str(value or '').strip()
    return _APP_TRACK_TOKEN_RE.sub('', raw).strip() or raw


_upload_session_lock = Lock()
_tonies_mutation_lock = Lock()
_upload_pw = None
_upload_browser = None
_upload_context = None
_upload_page = None
_upload_owner_thread_id = None


def _serialized_tonies_mutation(fn):
    def _wrapped(*args, **kwargs):
        with _tonies_mutation_lock:
            return fn(*args, **kwargs)
    return _wrapped


def _offloop_playwright(fn):
    """Run Playwright sync calls off asyncio-loop threads when needed."""
    def _wrapped(*args, **kwargs):
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            if loop and loop.is_running():
                out = {}
                err = {}

                def _runner():
                    try:
                        out["v"] = fn(*args, **kwargs)
                    except Exception as e:
                        err["e"] = e

                t = Thread(target=_runner, daemon=True)
                t.start()
                t.join()
                if "e" in err:
                    raise err["e"]
                return out.get("v")
        except RuntimeError:
            pass
        return fn(*args, **kwargs)
    return _wrapped


def _reset_upload_session_locked():
    global _upload_pw, _upload_browser, _upload_context, _upload_page, _upload_owner_thread_id
    try:
        if _upload_context is not None:
            _upload_context.close()
    except Exception:
        pass
    _upload_context = None
    _upload_page = None

    try:
        if _upload_browser is not None:
            _upload_browser.close()
    except Exception:
        pass
    _upload_browser = None

    try:
        if _upload_pw is not None:
            _upload_pw.stop()
    except Exception:
        pass
    _upload_pw = None
    _upload_owner_thread_id = None


def reset_upload_session() -> None:
    with _upload_session_lock:
        _reset_upload_session_locked()


def _get_upload_page():
    """Reuse a warm Playwright browser/context/page across uploads for speed.

    Playwright sync objects are thread-affine. If upload execution moves to a different
    Python thread (e.g., worker thread vs upload-only thread), fully recreate session.
    """
    import threading

    global _upload_pw, _upload_browser, _upload_context, _upload_page, _upload_owner_thread_id
    with _upload_session_lock:
        current_tid = threading.get_ident()

        if _upload_owner_thread_id is not None and _upload_owner_thread_id != current_tid:
            _reset_upload_session_locked()

        try:
            if _upload_page is not None and not _upload_page.is_closed():
                _upload_owner_thread_id = current_tid
                return _upload_page, _upload_context
        except Exception:
            _reset_upload_session_locked()

        try:
            if _upload_context is not None:
                _upload_context.close()
        except Exception:
            pass
        _upload_context = None
        _upload_page = None

        if _upload_pw is None:
            from playwright.sync_api import sync_playwright
            _upload_pw = sync_playwright().start()

        if _upload_browser is None:
            _upload_browser = _upload_pw.chromium.launch(headless=True)

        if settings.tonies_storage_state_file.exists():
            _upload_context = _upload_browser.new_context(storage_state=str(settings.tonies_storage_state_file))
        else:
            _upload_context = _upload_browser.new_context()

        _upload_page = _upload_context.new_page()
        _upload_page.set_default_timeout(12000)
        _upload_page.set_default_navigation_timeout(18000)
        _upload_owner_thread_id = current_tid
        return _upload_page, _upload_context


def _persist_storage_state_async(context):
    # Playwright sync objects are thread-affine; do not touch context from a background thread.
    # Keep this helper name for compatibility, but persist on the current thread.
    try:
        settings.tonies_storage_state_file.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(settings.tonies_storage_state_file))
    except Exception:
        pass



def _resolved_tonies_auth() -> tuple[str, str, str]:
    c = get_credentials()
    email = (c.get("email") or "").strip()
    password = (c.get("password") or "").strip()
    upload_url = settings.tonies_creative_upload_url
    return email, password, upload_url



@_serialized_tonies_mutation
def upload_to_tonies(mp3_path: Path, target_character_name: str = None, target_url: str = None, verify_strict: bool = False, _allow_thread_handoff: bool = True, tonies_email_override: str = None, tonies_password_override: str = None) -> None:
    t0 = time.time()
    logger.info("upload.start file=%s target_url=%s verify_strict=%s", mp3_path, bool(target_url), verify_strict)
    # Guard against accidental sync-API execution on a running asyncio loop thread.
    # If detected, hand off once to a plain worker thread and execute there.
    if _allow_thread_handoff:
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            if loop and loop.is_running():
                err: dict[str, Exception] = {}

                def _runner():
                    try:
                        upload_to_tonies(mp3_path, target_character_name, target_url, verify_strict, _allow_thread_handoff=False, tonies_email_override=tonies_email_override, tonies_password_override=tonies_password_override)
                    except Exception as e:
                        err["e"] = e

                t = Thread(target=_runner, daemon=True)
                t.start()
                t.join()
                if "e" in err:
                    raise err["e"]
                return
        except RuntimeError:
            pass

    # Always start from a fresh Playwright upload session per upload action.
    # This avoids stale cross-thread state after prior local-upload threads.
    reset_upload_session()

    target = target_character_name or settings.tonies_character_name
    tonies_email, tonies_password, configured_upload_url = _resolved_tonies_auth()
    tonies_email = (tonies_email_override or tonies_email or "").strip()
    tonies_password = (tonies_password_override or tonies_password or "").strip()
    if not tonies_email or not tonies_password:
        raise RuntimeError("Tonies credentials are required. Open /setup to configure credentials.")

    page, context = _get_upload_page()
    logger.info("upload.session_ready file=%s elapsed_ms=%d", mp3_path, int((time.time()-t0)*1000))

    debug_dir = settings.data_dir / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    nav_trace: list[str] = []

    def dump_debug(tag: str):
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        base = debug_dir / f"{ts}-{tag}"
        try:
            page.screenshot(path=str(base) + ".png", full_page=True)
        except Exception:
            pass

        # main page text + html
        try:
            body = page.locator("body").inner_text(timeout=5000)
            (Path(str(base) + ".txt")).write_text(body[:20000])
        except Exception:
            (Path(str(base) + ".txt")).write_text("<no body text>")
        try:
            (Path(str(base) + ".html")).write_text(page.content()[:500000])
        except Exception:
            pass

        # url + nav trace
        try:
            (Path(str(base) + ".url.txt")).write_text(page.url)
        except Exception:
            pass
        try:
            (Path(str(base) + ".trace.txt")).write_text("\n".join(nav_trace))
        except Exception:
            pass

        # frame dumps
        frames_out = []
        for i, f in enumerate(page.frames):
            entry = {"index": i, "url": f.url, "name": f.name}
            try:
                txt = f.inner_text("body")
                (Path(str(base) + f".frame{i}.txt")).write_text((txt or "")[:12000])
            except Exception:
                pass
            try:
                html = f.content()
                (Path(str(base) + f".frame{i}.html")).write_text((html or "")[:400000])
            except Exception:
                pass
            frames_out.append(entry)
        try:
            import json
            (Path(str(base) + ".frames.json")).write_text(json.dumps(frames_out, indent=2))
        except Exception:
            pass

        return str(base)

    # Go directly to creative upload URL first (as requested).
    direct_url = target_url or configured_upload_url or settings.tonies_app_url
    logger.info("upload.navigate direct_url=%s", direct_url)
    page.goto(direct_url, wait_until="domcontentloaded")
    _wait_for_tonies_editor_ready(page, 7000)
    nav_trace.append(f"open_direct: {page.url}")
    logger.info("upload.navigate_done url=%s elapsed_ms=%d", page.url, int((time.time()-t0)*1000))

    def has_upload_controls() -> bool:
        checks = [
            "button:has-text('Browse files')",
            "button:has-text('Browse Files')",
            "input[type='file']",
        ]
        for s in checks:
            try:
                if page.locator(s).first.count() > 0 and page.locator(s).first.is_visible(timeout=700):
                    return True
            except Exception:
                pass
        return False

    # If already authenticated and on creative page, skip login.
    if not has_upload_controls():
        # Verified flow for Tonies login:
        # 1) click SIGN IN/Login CTA on my.tonies.com login shell
        # 2) fill username/password on login.tonies.com
        # 3) click Continue
        signed_in_clicked = False
        clicked_sel = None
        for sel in [
            "button:has-text('SIGN IN')",
            "a:has-text('SIGN IN')",
            "text=SIGN IN",
            "button:has-text('Sign in now')",
            "a:has-text('Sign in now')",
            "text=Sign in now",
            "button:has-text('Login')",
            "a:has-text('Sign in')",
            "a:has-text('Login')",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=1200):
                    loc.click()
                    clicked_sel = sel
                    signed_in_clicked = True
                    break
            except Exception:
                pass

        # Role/text fallback if selector-based click misses.
        if not signed_in_clicked:
            for role_name in ["SIGN IN", "Sign in", "Sign in now", "Login"]:
                try:
                    loc = page.get_by_role("link", name=role_name).first
                    if loc.count() > 0 and loc.is_visible(timeout=900):
                        loc.click()
                        clicked_sel = f"role-link:{role_name}"
                        signed_in_clicked = True
                        break
                except Exception:
                    pass
                try:
                    loc = page.get_by_role("button", name=role_name).first
                    if loc.count() > 0 and loc.is_visible(timeout=900):
                        loc.click()
                        clicked_sel = f"role-button:{role_name}"
                        signed_in_clicked = True
                        break
                except Exception:
                    pass

        nav_trace.append(f"login_cta_clicked: {clicked_sel}")
        nav_trace.append(f"after_login_cta_url: {page.url}")
        if signed_in_clicked:
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1200)
            nav_trace.append(f"after_login_wait_url: {page.url}")

        # Fill login fields using multiple selectors.
        email_sel = ["#username", "input[name='username']", "input[type='email']"]
        pass_sel = ["#password", "input[name='password']", "input[type='password']"]
        email_ok = False
        pass_ok = False
        for s in email_sel:
            try:
                loc = page.locator(s).first
                if loc.count() > 0 and loc.is_visible(timeout=900):
                    loc.fill(tonies_email)
                    email_ok = True
                    break
            except Exception:
                pass
        for s in pass_sel:
            try:
                loc = page.locator(s).first
                if loc.count() > 0 and loc.is_visible(timeout=900):
                    loc.fill(tonies_password)
                    pass_ok = True
                    break
            except Exception:
                pass

        if email_ok and pass_ok:
            submitted = False
            submit_sel = None
            for sel in ["button:has-text('Continue')", settings.sel_submit, "button[type='submit']"]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=1000):
                        loc.click()
                        submit_sel = sel
                        submitted = True
                        break
                except Exception:
                    pass
            if not submitted:
                page.keyboard.press("Enter")
                submit_sel = "Enter"
            nav_trace.append(f"login_submitted_via: {submit_sel}")
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(350)
            nav_trace.append(f"after_submit_url: {page.url}")

        # Return to target upload page after login (or possible existing session).
        page.goto(direct_url, wait_until="domcontentloaded")
        _wait_for_tonies_editor_ready(page, 9000)
        nav_trace.append(f"back_to_direct_url: {page.url}")

    if not has_upload_controls():
        # Capture snapshots, clear stale session, and retry one forced fresh login.
        for i in range(1, 4):
            nav_trace.append(f"login_restricted_wait_{i}: {page.url}")
            dump_debug(f"login-restricted-wait{i}")
            page.wait_for_timeout(1200)

        # Remove stale storage state and retry once with a fresh browser context.
        try:
            if settings.tonies_storage_state_file.exists():
                settings.tonies_storage_state_file.unlink()
                nav_trace.append("cleared_stale_storage_state")
        except Exception:
            pass

        try:
            context.close()
        except Exception:
            pass
        context = _upload_browser.new_context()
        page = context.new_page()
        page.set_default_timeout(12000)
        page.set_default_navigation_timeout(18000)

        page.goto(direct_url, wait_until="domcontentloaded")
        _wait_for_tonies_editor_ready(page, 7000)
        nav_trace.append(f"fresh_retry_open_direct: {page.url}")

        # Force login CTA click on fresh context
        for sel in [
            "button:has-text('SIGN IN')",
            "a:has-text('SIGN IN')",
            "text=SIGN IN",
            "button:has-text('Sign in now')",
            "a:has-text('Sign in now')",
            "text=Sign in now",
            "button:has-text('Login')",
            "a:has-text('Sign in')",
            "a:has-text('Login')",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=1200):
                    loc.click()
                    nav_trace.append(f"fresh_retry_login_cta_clicked: {sel}")
                    break
            except Exception:
                pass

        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1200)

        page.fill("#username", tonies_email, timeout=30000)
        page.fill("#password", tonies_password, timeout=30000)
        submitted = False
        for sel in ["button:has-text('Continue')", settings.sel_submit, "button[type='submit']"]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=1000):
                    loc.click()
                    submitted = True
                    nav_trace.append(f"fresh_retry_submitted_via: {sel}")
                    break
            except Exception:
                pass
        if not submitted:
            page.keyboard.press("Enter")
            nav_trace.append("fresh_retry_submitted_via: Enter")

        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(350)
        page.goto(direct_url, wait_until="domcontentloaded")
        _wait_for_tonies_editor_ready(page, 9000)
        nav_trace.append(f"fresh_retry_back_to_direct: {page.url}")

    if not has_upload_controls():
        dbg = dump_debug("login-restricted")
        raise RuntimeError(f"Age/login restricted (debug: {dbg}.png/.txt/.html/.frames.json)")

    # Fallback non-direct character search flow if no direct URL is configured.
    if not configured_upload_url and target:
        page.fill(settings.sel_character_search, target)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1000)

    def _chapter_titles() -> list[str]:
        try:
            vals = page.evaluate(r'''() => {
              const rows=[...document.querySelectorAll("div[draggable='true'].chapter, div[draggable='true'][class*='ChapterDragNode']")];
              const primary = rows.map((r, idx) => {
                const input = r.querySelector("input[data-testid='input']");
                const label = r.querySelector("label[data-testid='input-label']");
                return ((input?.value || label?.textContent || `Chapter ${idx+1}`) || '').trim();
              }).filter(Boolean);

              if (primary.length) return primary;

              // Fallback for Tonies DOM variants where chapter nodes are rendered differently.
              const fallbackNodes = [
                ...document.querySelectorAll("input[data-testid='input']"),
                ...document.querySelectorAll("label[data-testid='input-label']"),
                ...document.querySelectorAll("[class*='chapter'] input"),
                ...document.querySelectorAll("[class*='chapter'] label"),
              ];
              const uniq = [];
              const seen = new Set();
              for (const n of fallbackNodes) {
                const t = String(n?.value || n?.textContent || '').trim();
                if (!t) continue;
                if (seen.has(t)) continue;
                seen.add(t);
                uniq.push(t);
              }
              return uniq;
            }''')
            return [str(v).strip() for v in (vals or []) if str(v).strip()]
        except Exception:
            return []

    before_titles = _chapter_titles()
    before_count = len(before_titles)

    # Upload with a short internal app token to avoid duplicate-title ambiguity.
    # Keep the user-facing stem intact; the frontend strips this token from display.
    stem_base = _strip_app_track_token(mp3_path.stem) or mp3_path.stem
    unique_token = uuid4().hex[:6]
    internal_upload_name = f"{stem_base} [oc:{unique_token}]{mp3_path.suffix}"
    upload_payload = {
        "name": internal_upload_name,
        "mimeType": "audio/mpeg",
        "buffer": mp3_path.read_bytes(),
    }

    # Trigger upload and set file.
    # Preferred path: click Browse files and handle native chooser event.
    attached = False
    attach_method = None
    browse_selectors = [
        "button:has-text('Browse files')",
        "button:has-text('Browse Files')",
        "a:has-text('Browse files')",
        "a:has-text('Browse Files')",
        settings.sel_upload_button,
    ]

    for sel in browse_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible(timeout=1200):
                with page.expect_file_chooser(timeout=4000) as fc_info:
                    loc.click()
                chooser = fc_info.value
                chooser.set_files(upload_payload)
                attached = True
                attach_method = f"chooser:{sel}"
                break
        except Exception:
            pass

    # Fallback path: set file directly on every visible/hidden input[type=file].
    if not attached:
        try:
            inputs = page.locator("input[type='file']")
            for i in range(inputs.count()):
                try:
                    inputs.nth(i).set_input_files(upload_payload)
                    attached = True
                    attach_method = f"input:index:{i}"
                    break
                except Exception:
                    pass
        except Exception:
            pass

    if not attached:
        dbg = dump_debug("attach-failed")
        raise RuntimeError(f"Could not attach file via file chooser or file input (debug: {dbg}.png/.txt)")

    def _file_input_count() -> int:
        try:
            val = page.evaluate("""
              () => {
                const inp = document.querySelector("input[data-testid='creative-tonies-file-upload'], input[type='file']");
                return inp?.files?.length || 0;
              }
            """)
            return int(val or 0)
        except Exception:
            return 0

    file_count = _file_input_count()

    # If chooser path didn't actually set files, force-set on known input.
    if file_count == 0:
        try:
            inp = page.locator("input[data-testid='creative-tonies-file-upload']").first
            if inp.count() > 0:
                inp.set_input_files(upload_payload)
                attach_method = (attach_method or "") + "|fallback:data-testid"
        except Exception:
            pass
        file_count = _file_input_count()

    nav_trace.append(f"attach_method: {attach_method}")
    nav_trace.append(f"internal_upload_name: {internal_upload_name}")
    nav_trace.append(f"file_input_count_after_attach: {file_count}")
    logger.info("upload.file_attached method=%s file_count=%s elapsed_ms=%d", attach_method, file_count, int((time.time()-t0)*1000))

    def _has_upload_success_signal() -> bool:
        try:
            txt = (page.locator("body").inner_text(timeout=3000) or "").lower()
        except Exception:
            txt = ""
        # Tonies indicators observed on manual successful uploads.
        if "finished!" in txt or "finished" in txt:
            return True
        if "the contents were saved successfully" in txt:
            return True
        return False

    def _upload_row_state() -> dict:
        try:
            return page.evaluate(r'''(token) => {
              const bodyText = (document.body?.innerText || '').toLowerCase();
              const rows = [...document.querySelectorAll("div[draggable='true'].chapter, div[draggable='true'][class*='ChapterDragNode'], [data-testid*='upload'], [class*='upload'], [class*='Upload']")];
              const norm = (s) => String(s || '').toLowerCase();
              const hit = rows.find((el) => {
                const txt = norm(el.innerText || el.textContent || '');
                return token && txt.includes(token.toLowerCase());
              });
              const removeBtn = hit ? hit.querySelector("button[aria-label*='Remove'], button[title*='Remove'], button[class*='Remove'], button") : null;
              return {
                bodyText,
                rowFound: !!hit,
                rowText: hit ? String(hit.innerText || hit.textContent || '').trim().slice(0, 500) : '',
                hasRemoveButton: !!removeBtn,
                hasFinishedText: bodyText.includes('finished!') || bodyText.includes('finished'),
                hasProcessingText: bodyText.includes('processing') || bodyText.includes('uploading'),
                hasAssignedText: bodyText.includes('successfully assigned'),
              };
            }''', stem_base)
        except Exception:
            return {
                "bodyText": "",
                "rowFound": False,
                "rowText": "",
                "hasRemoveButton": False,
                "hasFinishedText": False,
                "hasProcessingText": False,
                "hasAssignedText": False,
            }

    # Wait for Tonies upload UI to acknowledge/process the attached file before saving.
    upload_state = _upload_row_state()
    nav_trace.append(f"upload_row_initial_found: {upload_state.get('rowFound')}")
    nav_trace.append(f"upload_row_initial_remove: {upload_state.get('hasRemoveButton')}")
    nav_trace.append(f"upload_row_initial_finished: {upload_state.get('hasFinishedText')}")

    upload_ready = False
    for idx in range(24):
        if idx > 0:
            page.wait_for_timeout(1000)
        upload_state = _upload_row_state()
        nav_trace.append(
            f"upload_row_poll_{idx+1}: found={upload_state.get('rowFound')} remove={upload_state.get('hasRemoveButton')} finished={upload_state.get('hasFinishedText')} processing={upload_state.get('hasProcessingText')}"
        )
        if upload_state.get('hasFinishedText') or upload_state.get('hasAssignedText'):
            upload_ready = True
            nav_trace.append(f"upload_row_ready_signal_poll_{idx+1}: success_text")
            break
        if upload_state.get('rowFound') and upload_state.get('hasRemoveButton'):
            upload_ready = True
            nav_trace.append(f"upload_row_ready_signal_poll_{idx+1}: row_with_remove")
            break

    if not upload_ready:
        nav_trace.append(f"upload_row_last_text: {str(upload_state.get('rowText') or '')[:300]}")

    # Prefer explicit sticky save button by data-testid.
    save_btn = None
    for sel in [
        "button[data-testid='edit-save-content-button']",
        "button:has-text('SAVE CONTENT')",
        "button:has-text('Save content')",
        "button:has-text('Save')",
        "button:has-text('SAVE')",
    ]:
        try:
            b = page.locator(sel).first
            if b.count() > 0 and b.is_visible(timeout=800):
                save_btn = b
                nav_trace.append(f"save_button_selector: {sel}")
                break
        except Exception:
            pass

    # Fast path: when Tonies already reports "Finished" / saved-success, avoid extra save click.
    saw_success_signal = False
    for _ in range(5):
        if _has_upload_success_signal():
            saw_success_signal = True
            nav_trace.append("upload_success_signal_seen: true")
            break
        page.wait_for_timeout(700)

    if (not saw_success_signal) and save_btn is not None:
        try:
            page.wait_for_function(
                "el => !!el && !el.disabled && el.getAttribute('aria-disabled') !== 'true'",
                arg=save_btn.element_handle(),
                timeout=25000,
            )
        except Exception:
            pass
        try:
            is_disabled = save_btn.evaluate("el => !!el.disabled || el.getAttribute('aria-disabled') === 'true'")
            nav_trace.append(f"save_button_disabled_before_click: {is_disabled}")
        except Exception:
            pass
        try:
            save_btn.click(timeout=5000)
            nav_trace.append("save_button_clicked: true")
            logger.info("upload.save_clicked elapsed_ms=%d", int((time.time()-t0)*1000))
        except Exception:
            try:
                save_btn.click(timeout=5000, force=True)
                nav_trace.append("save_button_clicked_force: true")
            except Exception:
                pass
        page.wait_for_timeout(900)

    # Verification: require chapter-list change (count grows or new matching title appears).
    def _norm(s: str) -> str:
        import re
        return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

    before_norm_set = {_norm(t) for t in before_titles if t}
    stem_norm = _norm(mp3_path.stem)
    stem_probe = stem_norm[:14] if stem_norm else ""

    def _is_confirmed(cur_titles: list[str]) -> bool:
        cur_count = len(cur_titles)
        if cur_count > before_count:
            return True
        cur_norm = {_norm(t) for t in cur_titles if t}
        # New normalized title appeared in list.
        if any(n and n not in before_norm_set for n in cur_norm):
            return True
        if stem_probe:
            for t in cur_titles:
                tn = _norm(t)
                if tn and (stem_probe in tn or tn in stem_norm):
                    return True
        return False

    confirmed = False
    last_after_count = before_count

    def _current_body_text() -> str:
        try:
            return page.locator("body").inner_text(timeout=8000) or ""
        except Exception:
            return ""

    def _hint_seen(body_text: str) -> bool:
        body_low = body_text.lower()
        hints = ["upload complete", "processing", "transferred", "successfully assigned", "ready to start in", "finished"]
        return any(h in body_low for h in hints)

    def _resolve_editor_url() -> str:
        revisit_url = target_url or configured_upload_url or settings.tonies_app_url
        try:
            cur_url = page.url or ""
        except Exception:
            cur_url = ""
        if "/refresh?" in cur_url and "relatedUrl=" in cur_url:
            try:
                from urllib.parse import urlparse, parse_qs, unquote
                parsed = urlparse(cur_url)
                related = parse_qs(parsed.query).get("relatedUrl", [""])[0]
                related = unquote(related or "").strip()
                if related:
                    if related.startswith("http://") or related.startswith("https://"):
                        revisit_url = related
                    elif related.startswith("/"):
                        from urllib.parse import urljoin
                        revisit_url = urljoin(cur_url, related)
            except Exception:
                pass
        return revisit_url

    def _return_to_editor(reason: str) -> str:
        revisit_url = _resolve_editor_url()
        nav_trace.append(f"return_to_editor_{reason}: {revisit_url}")
        page.goto(revisit_url, wait_until="domcontentloaded")
        _wait_for_tonies_editor_ready(page, 12000)
        page.wait_for_timeout(1200)
        return revisit_url

    # Fast verification window first, but only chapter-list changes count as confirmation.
    for _ in range(5):
        page.wait_for_timeout(500)
        if _has_upload_success_signal():
            nav_trace.append("verify_fast_signal_seen: true")
        cur_titles = _chapter_titles()
        last_after_count = len(cur_titles)
        if _is_confirmed(cur_titles):
            confirmed = True
            nav_trace.append("verify_fast_list_confirmed: true")
            break

    body = ""
    hint_seen = False

    # Strict mode: revisit the creative page and allow longer bounded reconciliation.
    if verify_strict and not confirmed:
        body = _current_body_text()
        hint_seen = _hint_seen(body)
        nav_trace.append(f"verify_pending_hint_seen_before_reload: {hint_seen}")
        revisit_url = _resolve_editor_url()

        for attempt in range(1, 4):
            try:
                page.reload(wait_until="domcontentloaded")
                nav_trace.append(f"verify_reload_attempt_{attempt}: reload")
            except Exception:
                page.goto(revisit_url, wait_until="domcontentloaded")
                nav_trace.append(f"verify_reload_attempt_{attempt}: goto")

            if "/refresh" in (page.url or "") or not has_upload_controls():
                revisit_url = _return_to_editor(f"verify_reload_attempt_{attempt}")
            else:
                _wait_for_tonies_editor_ready(page, 12000)
                page.wait_for_timeout(1200)

            cur_titles = _chapter_titles()
            last_after_count = len(cur_titles)
            nav_trace.append(f"verify_reload_attempt_{attempt}_after_count: {last_after_count}")
            if _is_confirmed(cur_titles):
                confirmed = True
                nav_trace.append(f"verify_reload_attempt_{attempt}_confirmed: true")
                break

            for slow_idx in range(5):
                page.wait_for_timeout(1500)
                cur_titles = _chapter_titles()
                last_after_count = len(cur_titles)
                nav_trace.append(f"verify_reload_attempt_{attempt}_poll_{slow_idx+1}_after_count: {last_after_count}")
                if _is_confirmed(cur_titles):
                    confirmed = True
                    nav_trace.append(f"verify_reload_attempt_{attempt}_poll_{slow_idx+1}_confirmed: true")
                    break

            if confirmed:
                break

            body = _current_body_text()
            hint_seen = _hint_seen(body)
            nav_trace.append(f"verify_reload_attempt_{attempt}_hint_seen: {hint_seen}")

    if not confirmed:
        logger.warning("upload.verify_pending file=%s elapsed_ms=%d before_count=%d after_count=%d", mp3_path, int((time.time()-t0)*1000), before_count, last_after_count)
        if not body:
            body = _current_body_text()
        hint_seen = _hint_seen(body)
        body_low = body.lower()

        # Non-strict mode may accept UI hints, but strict mode must not.
        if (not verify_strict) and hint_seen:
            confirmed = True
            nav_trace.append("verify_hint_accept_non_strict: true")

        if not confirmed:
            dbg = dump_debug("upload-verify-failed")
            raise RuntimeError(
                f"Upload was not confirmed on my.tonies.com (before={before_count}, after={last_after_count}, hints={hint_seen}, "
                f"save_success_signal={_has_upload_success_signal()}, processing_hint={'processing' in body_low}) "
                f"(debug: {dbg}.png/.txt/.html)"
            )

    # Persist authenticated browser session for subsequent uploads (non-blocking).
    _persist_storage_state_async(context)
    logger.info("upload.completed file=%s elapsed_ms=%d", mp3_path, int((time.time()-t0)*1000))



def _wait_for_tonies_editor_ready(page, timeout_ms: int = 7000):
    selectors = [
        "div[draggable='true'].chapter",
        "div[draggable='true'][class*='ChapterDragNode']",
        "input[data-testid='creative-tonies-file-upload']",
        "button[data-testid='edit-save-content-button']",
    ]

    deadline = time.time() + max(1.0, timeout_ms / 1000.0)
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    return
            except Exception:
                pass
        try:
            body = (page.locator('body').inner_text(timeout=500) or '').lower()
            if 'min free' in body or 'finished' in body or 'save content' in body:
                return
        except Exception:
            pass
        page.wait_for_timeout(220)


def _open_tonies_editor(page, target_url: str):
    tonies_email, tonies_password, configured_upload_url = _resolved_tonies_auth()
    direct_url = target_url or configured_upload_url or settings.tonies_app_url
    page.goto(direct_url, wait_until="domcontentloaded")
    _wait_for_tonies_editor_ready(page, 7000)

    if page.locator("#username").count() > 0 and page.locator("#password").count() > 0:
        if not tonies_email or not tonies_password:
            raise RuntimeError("Tonies credentials are required. Open /setup to configure credentials.")
        page.fill("#username", tonies_email, timeout=30000)
        page.fill("#password", tonies_password, timeout=30000)
        for sel in ["button:has-text('Continue')", "button[type='submit']"]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=1000):
                    loc.click()
                    break
            except Exception:
                pass
        page.wait_for_load_state("domcontentloaded")
        page.goto(direct_url, wait_until="domcontentloaded")
        _wait_for_tonies_editor_ready(page, 9000)



def _extract_tonies_content(page) -> dict:
    body = page.locator("body").inner_text(timeout=10000)
    import re
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*min free", body, re.IGNORECASE)
    free_minutes = int(m.group(1)) if m else None
    total_minutes = int(m.group(2)) if m else 90

    chapters = page.evaluate(r'''() => {
      const rows=[...document.querySelectorAll("div[draggable='true'].chapter, div[draggable='true'][class*='ChapterDragNode']")];
      return rows.map((r, idx) => {
        const input = r.querySelector("input[data-testid='input']");
        const label = r.querySelector("label[data-testid='input-label']");
        const txt = (r.innerText||"").trim();
        const m = txt.match(/\d{2}:\d{2}:\d{2}/);
        const actual = (input?.value || "").trim();
        const fallback = (label?.textContent || `Chapter ${idx+1}`).trim();
        const chapterId = (
          r.getAttribute("data-content-id")
          || r.getAttribute("data-contentid")
          || r.getAttribute("data-track-id")
          || r.getAttribute("data-trackid")
          || r.getAttribute("data-id")
          || r.getAttribute("data-testid")
          || r.getAttribute("aria-label")
          || r.id
          || ""
        ).trim();
        const title = (actual || fallback).trim();
        const tokenMatch = title.match(/\s\[(?:oc:)?([a-f0-9]{4,12})\]$/i);
        return {
          index: idx,
          chapter_id: chapterId,
          title,
          display_title: title.replace(/\s\[(?:oc:)?[a-f0-9]{4,12}\]$/i, '').trim() || title,
          app_track_token: tokenMatch ? String(tokenMatch[1] || '').toLowerCase() : "",
          duration: m ? m[0] : "",
        };
      });
    }''')

    used_minutes = None
    if free_minutes is not None and total_minutes is not None:
        used_minutes = max(total_minutes - free_minutes, 0)

    try:
        summary = [f"{idx}:{(c.get('chapter_id') or '').strip()}|{(c.get('title') or '').strip()}|{(c.get('duration') or '').strip()}" for idx, c in enumerate(chapters[:30])]
        logger.info("tonies.content.extract count=%d free=%s summary=%s", len(chapters), free_minutes, summary)
    except Exception:
        pass

    return {
        "free_minutes": free_minutes,
        "total_minutes": total_minutes,
        "used_minutes": used_minutes,
        "chapters": chapters,
    }



def get_tonies_content(target_url: str) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(settings.tonies_storage_state_file) if settings.tonies_storage_state_file.exists() else None)
        page = context.new_page()
        _open_tonies_editor(page, target_url)
        out = _extract_tonies_content(page)
        try:
            summary = [f"{idx}:{(c.get('chapter_id') or '').strip()}|{(c.get('title') or '').strip()}|{(c.get('duration') or '').strip()}" for idx, c in enumerate((out.get('chapters') or [])[:30])]
            logger.info("tonies.content.fetch target=%s count=%d summary=%s", target_url, len(out.get('chapters') or []), summary)
        except Exception:
            pass
        try:
            context.storage_state(path=str(settings.tonies_storage_state_file))
        except Exception:
            pass
        context.close()
        browser.close()
        return out



@_serialized_tonies_mutation
def delete_tonies_chapter(target_url: str, index: int, chapter_title: str | None = None, chapter_occurrence: int = 0, chapter_id: str | None = None, chapter_fingerprint: str | None = None, app_track_token: str | None = None) -> dict:
    from playwright.sync_api import sync_playwright

    def _norm(s: str) -> str:
        return " ".join(str(s or "").replace("_", " ").strip().lower().split())

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(settings.tonies_storage_state_file) if settings.tonies_storage_state_file.exists() else None)
        page = context.new_page()
        _open_tonies_editor(page, target_url)

        # Handle any browser confirm dialogs defensively.
        page.on("dialog", lambda d: d.accept())

        row_sel = "div[draggable='true'].chapter, div[draggable='true'][class*='ChapterDragNode']"
        rows = page.locator(row_sel)
        count_before = rows.count()

        resolved_index = index
        title_norm = _norm(chapter_title)
        chapter_id_norm = _norm(chapter_id)
        chapter_fp_norm = _norm(chapter_fingerprint)
        app_track_token_norm = _norm(app_track_token)

        def _row_id(i: int) -> str:
            try:
                rid = rows.nth(i).evaluate(r'''(el) => {
                  const keys = [
                    el.getAttribute('data-content-id'),
                    el.getAttribute('data-contentid'),
                    el.getAttribute('data-track-id'),
                    el.getAttribute('data-trackid'),
                    el.getAttribute('data-id'),
                    el.getAttribute('data-testid'),
                    el.getAttribute('aria-label'),
                    el.id,
                  ];
                  const first = keys.find((x) => x && String(x).trim());
                  return String(first || '').trim();
                }''')
            except Exception:
                rid = ""
            return _norm(rid)

        def _row_token(i: int) -> str:
            try:
                raw_title = rows.nth(i).evaluate(r'''(el, idx) => {
                  const input = el.querySelector("input[data-testid='input']");
                  const label = el.querySelector("label[data-testid='input-label']");
                  return String(input?.value || label?.textContent || `Chapter ${Number(idx) + 1}` || '').trim();
                }''', i)
            except Exception:
                raw_title = ""
            return _norm(_extract_app_track_token(raw_title))

        def _row_fp(i: int) -> str:
            try:
                fp = rows.nth(i).evaluate(r'''(el, idx) => {
                  const input = el.querySelector("input[data-testid='input']");
                  const label = el.querySelector("label[data-testid='input-label']");
                  const txt = (el.innerText || "").trim();
                  const m = txt.match(/\d{2}:\d{2}:\d{2}/);
                  const title = (input?.value || label?.textContent || `Chapter ${Number(idx) + 1}` || '').trim();
                  const dur = (m ? m[0] : '').trim();
                  return `${title}|${dur}`;
                }''', i)
            except Exception:
                fp = ""
            return _norm(fp)

        def _row_match_rank(i: int) -> int:
            if not title_norm:
                return 0
            try:
                txt = rows.nth(i).inner_text(timeout=1200)
            except Exception:
                txt = ""
            row_norm = _norm(txt)
            if not row_norm:
                return 0
            if row_norm == title_norm:
                return 3
            if title_norm in row_norm:
                return 2
            if row_norm in title_norm:
                return 1
            return 0

        # Prefer exact index when still valid and matching expected immutable identity.
        if resolved_index >= 0 and resolved_index < count_before:
            if app_track_token_norm:
                if _row_token(resolved_index) != app_track_token_norm:
                    resolved_index = -1
            elif chapter_id_norm:
                row_id = _row_id(resolved_index)
                if not row_id or row_id != chapter_id_norm:
                    resolved_index = -1
            elif chapter_fp_norm:
                if _row_fp(resolved_index) != chapter_fp_norm:
                    resolved_index = -1
            elif title_norm and _row_match_rank(resolved_index) == 0:
                resolved_index = -1

        if resolved_index < 0 or resolved_index >= count_before:
            resolved_index = -1
            if app_track_token_norm:
                token_matches: list[int] = []
                for i in range(count_before):
                    if _row_token(i) == app_track_token_norm:
                        token_matches.append(i)
                if len(token_matches) == 1:
                    resolved_index = token_matches[0]
                elif token_matches and index >= 0:
                    token_matches.sort(key=lambda x: abs(x - index))
                    resolved_index = token_matches[0]

            if resolved_index < 0 and chapter_id_norm:
                id_matches: list[int] = []
                for i in range(count_before):
                    row_id = _row_id(i)
                    if row_id and row_id == chapter_id_norm:
                        id_matches.append(i)
                if len(id_matches) == 1:
                    resolved_index = id_matches[0]
                elif id_matches and index >= 0:
                    id_matches.sort(key=lambda x: abs(x - index))
                    resolved_index = id_matches[0]

            if resolved_index < 0 and chapter_fp_norm:
                fp_matches: list[int] = []
                for i in range(count_before):
                    if _row_fp(i) == chapter_fp_norm:
                        fp_matches.append(i)
                if len(fp_matches) == 1:
                    resolved_index = fp_matches[0]
                elif fp_matches and index >= 0:
                    fp_matches.sort(key=lambda x: abs(x - index))
                    resolved_index = fp_matches[0]

            if resolved_index < 0 and title_norm:
                exact_matches: list[int] = []
                partial_matches: list[int] = []
                loose_matches: list[int] = []
                for i in range(count_before):
                    rank = _row_match_rank(i)
                    if rank >= 3:
                        exact_matches.append(i)
                    elif rank == 2:
                        partial_matches.append(i)
                    elif rank == 1:
                        loose_matches.append(i)

                matches = exact_matches or partial_matches or loose_matches
                if matches:
                    occ = max(1, int(chapter_occurrence or 1))
                    pick = min(len(matches), occ) - 1
                    resolved_index = matches[pick]
            if resolved_index < 0:
                raise RuntimeError(f"Invalid chapter index ({index}) for {count_before} rows")

        row = rows.nth(resolved_index)
        btn = row.locator("button[class*='RemoveChapterButton'], button[data-testid*='remove'], button[aria-label*='Remove'], button[title*='Remove'], button").first

        # Remove button can be icon-only and sometimes needs hover/force click.
        clicked = False
        try:
            row.hover(timeout=2000)
        except Exception:
            pass

        for force in [False, True]:
            if clicked:
                break
            try:
                btn.click(timeout=5000, force=force)
                clicked = True
            except Exception:
                pass

        if not clicked:
            try:
                row.evaluate("el => { const b = el.querySelector(\"button[class*='RemoveChapterButton'],button\"); if (b) b.click(); }")
                clicked = True
            except Exception:
                pass

        if not clicked:
            raise RuntimeError("Could not click chapter delete button")

        # Some flows open a confirm CTA after clicking row-delete.
        for sel in [
            "button:has-text('DELETE CONTENT')",
            "button:has-text('Delete content')",
            "button:has-text('Delete')",
        ]:
            try:
                confirm_btn = page.locator(sel).first
                if confirm_btn.count() > 0 and confirm_btn.is_visible(timeout=700):
                    confirm_btn.click(timeout=5000)
                    break
            except Exception:
                pass

        # Wait for list to reflect removal.
        try:
            page.wait_for_function(
                "(sel, c) => document.querySelectorAll(sel).length < c",
                arg=["div[draggable='true'].chapter, div[draggable='true'][class*='ChapterDragNode']", count_before],
                timeout=10000,
            )
        except Exception:
            page.wait_for_timeout(1000)

        saved = False
        save_selectors = [
            "button:has-text('SAVE CONTENT')",
            "button:has-text('Save content')",
            "button:has-text('Save')",
            "button[class*='kmzsiT']:has-text('SAVE')",
            "button[class*='kmzsiT']:has-text('content')",
        ]
        for sel in save_selectors:
            try:
                b = page.locator(sel).first
                if b.count() > 0 and b.is_visible(timeout=900):
                    b.click(timeout=5000)
                    saved = True
                    break
            except Exception:
                pass

        if not saved:
            # Last-resort DOM search by text content.
            try:
                clicked = page.evaluate("""
                  () => {
                    const nodes = [...document.querySelectorAll('button,[role="button"],input[type="button"],input[type="submit"]')];
                    const n = nodes.find(el => ((el.innerText||el.value||'').toLowerCase().includes('save content') || (el.innerText||el.value||'').toLowerCase() === 'save'));
                    if (!n) return false;
                    n.click();
                    return true;
                  }
                """)
                saved = bool(clicked)
            except Exception:
                pass

        if not saved:
            raise RuntimeError("Could not find Save Content button after delete")

        page.wait_for_timeout(1800)

        out = _extract_tonies_content(page)
        try:
            summary = [f"{idx}:{(c.get('chapter_id') or '').strip()}|{(c.get('title') or '').strip()}|{(c.get('duration') or '').strip()}" for idx, c in enumerate((out.get('chapters') or [])[:30])]
            logger.info("tonies.delete.result target=%s resolved_index=%s count_before=%d count_after=%d summary=%s", target_url, resolved_index, count_before, len(out.get('chapters') or []), summary)
        except Exception:
            pass
        try:
            context.storage_state(path=str(settings.tonies_storage_state_file))
        except Exception:
            pass
        context.close()
        browser.close()
        return out



@_serialized_tonies_mutation
def delete_all_tonies_content(target_url: str) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(settings.tonies_storage_state_file) if settings.tonies_storage_state_file.exists() else None)
        page = context.new_page()
        _open_tonies_editor(page, target_url)

        opened = False
        for sel in [
            "a:has-text('Delete all content')",
            "button:has-text('Delete all content')",
            "text=Delete all content",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=1200):
                    loc.click(timeout=5000)
                    opened = True
                    break
            except Exception:
                pass

        if not opened:
            # Fallback JS click by text scan.
            try:
                opened = bool(page.evaluate("""
                  () => {
                    const nodes = [...document.querySelectorAll('a,button,[role="button"]')];
                    const n = nodes.find(el => ((el.innerText||'').trim().toLowerCase() === 'delete all content'));
                    if (!n) return false;
                    n.click();
                    return true;
                  }
                """))
            except Exception:
                opened = False

        if not opened:
            raise RuntimeError("Could not find 'Delete all content' action")

        page.wait_for_timeout(450)

        confirmed = False
        for sel in [
            "button:has-text('DELETE ALL CONTENT ON THE CREATIVE-TONIE')",
            "button:has-text('Delete all content on the Creative-Tonie')",
            "button:has-text('Delete all content')",
            "button:has-text('DELETE ALL CONTENT')",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible(timeout=2200):
                    loc.click(timeout=5000)
                    confirmed = True
                    break
            except Exception:
                pass

        if not confirmed:
            try:
                confirmed = bool(page.evaluate("""
                  () => {
                    const nodes = [...document.querySelectorAll('button,[role="button"],input[type="button"],input[type="submit"]')];
                    const n = nodes.find(el => (el.innerText||el.value||'').toLowerCase().includes('delete all content'));
                    if (!n) return false;
                    n.click();
                    return true;
                  }
                """))
            except Exception:
                confirmed = False

        if not confirmed:
            raise RuntimeError("Could not confirm 'Delete all content' action")

        page.wait_for_timeout(1200)

        # On Tonies web, bulk delete still requires explicit Save content.
        saved = False
        for sel in [
            "button[data-testid='edit-save-content-button']",
            "button:has-text('SAVE CONTENT')",
            "button:has-text('Save content')",
            "button:has-text('Save')",
        ]:
            try:
                b = page.locator(sel).first
                if b.count() > 0 and b.is_visible(timeout=1200):
                    b.click(timeout=5000)
                    saved = True
                    break
            except Exception:
                pass

        if not saved:
            try:
                saved = bool(page.evaluate("""
                  () => {
                    const nodes = [...document.querySelectorAll('button,[role="button"],input[type="button"],input[type="submit"]')];
                    const n = nodes.find(el => {
                      const t = (el.innerText || el.value || '').trim().toLowerCase();
                      return t.includes('save content') || t === 'save';
                    });
                    if (!n) return false;
                    n.click();
                    return true;
                  }
                """))
            except Exception:
                saved = False

        if not saved:
            raise RuntimeError("Could not find Save Content button after delete-all")

        page.wait_for_timeout(1800)

        out = _extract_tonies_content(page)
        try:
            context.storage_state(path=str(settings.tonies_storage_state_file))
        except Exception:
            pass
        context.close()
        browser.close()
        return out


@_serialized_tonies_mutation
def rename_tonies_chapter(target_url: str, index: int, title: str) -> dict:
    from playwright.sync_api import sync_playwright

    new_title = (title or "").strip()
    if not new_title:
        raise RuntimeError("Title cannot be empty")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(settings.tonies_storage_state_file) if settings.tonies_storage_state_file.exists() else None)
        page = context.new_page()
        _open_tonies_editor(page, target_url)

        row_sel = "div[draggable='true'].chapter, div[draggable='true'][class*='ChapterDragNode']"
        rows = page.locator(row_sel)
        count = rows.count()
        if index < 0 or index >= count:
            raise RuntimeError("Invalid chapter index")

        row = rows.nth(index)
        inp = row.locator("input[data-testid='input']").first
        if inp.count() == 0:
            raise RuntimeError("Could not find chapter title input")

        inp.click(timeout=5000)
        inp.fill(new_title, timeout=5000)
        inp.press("Tab")

        saved = False
        for sel in [
            "button[data-testid='edit-save-content-button']",
            "button:has-text('SAVE CONTENT')",
            "button:has-text('Save content')",
            "button:has-text('Save')",
        ]:
            try:
                b = page.locator(sel).first
                if b.count() > 0 and b.is_visible(timeout=900):
                    b.click(timeout=5000)
                    saved = True
                    break
            except Exception:
                pass
        if not saved:
            raise RuntimeError("Could not find Save Content button after rename")

        page.wait_for_timeout(1800)

        out = _extract_tonies_content(page)
        try:
            context.storage_state(path=str(settings.tonies_storage_state_file))
        except Exception:
            pass
        context.close()
        browser.close()
        return out



@_offloop_playwright
@_serialized_tonies_mutation
def reorder_tonies_chapter(target_url: str, from_index: int, to_index: int) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(settings.tonies_storage_state_file) if settings.tonies_storage_state_file.exists() else None)
        page = context.new_page()
        _open_tonies_editor(page, target_url)

        row_sel = "div[draggable='true'].chapter, div[draggable='true'][class*='ChapterDragNode']"
        rows = page.locator(row_sel)
        count = rows.count()
        if from_index < 0 or to_index < 0 or from_index >= count or to_index >= count:
            raise RuntimeError("Invalid reorder indices")

        def _titles() -> list[str]:
            try:
                vals = page.evaluate(r'''() => {
                  const rows=[...document.querySelectorAll("div[draggable='true'].chapter, div[draggable='true'][class*='ChapterDragNode']")];
                  return rows.map((r, idx) => {
                    const input=r.querySelector("input[data-testid='input']");
                    const label=r.querySelector("label[data-testid='input-label']");
                    return ((input?.value || label?.textContent || `Chapter ${idx+1}`) || '').trim();
                  });
                }''')
                return [str(v).strip() for v in (vals or [])]
            except Exception:
                return []

        # Robust stepwise reorder: after each drag, re-read current index of moved title.
        titles0 = _titles()
        moving_title = titles0[from_index] if from_index < len(titles0) else ""
        target = to_index
        max_steps = max(8, count * 3)
        steps = 0

        while steps < max_steps:
            cur_titles = _titles()
            try:
                cur = cur_titles.index(moving_title) if moving_title else from_index
            except ValueError:
                cur = from_index

            if cur == target:
                break

            step = 1 if target > cur else -1
            nxt = cur + step
            if nxt < 0 or nxt >= count:
                break

            src = page.locator(row_sel).nth(cur)
            dst = page.locator(row_sel).nth(nxt)
            try:
                src.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
            try:
                dst.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass

            src.drag_to(dst)
            page.wait_for_timeout(420)
            steps += 1

        page.wait_for_timeout(650)

        final_titles = _titles()
        try:
            final_idx = final_titles.index(moving_title) if moving_title else to_index
        except ValueError:
            final_idx = to_index
        if final_idx != to_index:
            raise RuntimeError(f"Reorder did not reach target index (wanted {to_index}, got {final_idx})")

        saved = False
        for sel in [
            "button[data-testid='edit-save-content-button']",
            "button:has-text('SAVE CONTENT')",
            "button:has-text('Save content')",
            "button:has-text('Save')",
        ]:
            try:
                b = page.locator(sel).first
                if b.count() > 0 and b.is_visible(timeout=900):
                    b.click(timeout=5000)
                    saved = True
                    break
            except Exception:
                pass
        if not saved:
            raise RuntimeError("Could not find Save Content button after reorder")

        page.wait_for_timeout(1800)

        out = _extract_tonies_content(page)
        try:
            context.storage_state(path=str(settings.tonies_storage_state_file))
        except Exception:
            pass
        context.close()
        browser.close()
        return out



@_offloop_playwright
def list_creative_tonies() -> list[dict]:
    from playwright.sync_api import sync_playwright
    tonies_email, tonies_password, _ = _resolved_tonies_auth()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        if settings.tonies_storage_state_file.exists():
            context = browser.new_context(storage_state=str(settings.tonies_storage_state_file))
        else:
            context = browser.new_context()
        page = context.new_page()

        base_url = settings.tonies_app_url.rstrip('/')
        creative_url = f"{base_url}/creative-tonies"
        page.goto(creative_url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")

        def do_login_if_present():
            # If login gate appears, click in and submit credentials.
            for sel in ["button:has-text('SIGN IN')", "a:has-text('SIGN IN')", "text=SIGN IN", "button:has-text('Login')", "a:has-text('Login')"]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=900):
                        loc.click()
                        page.wait_for_timeout(1000)
                        break
                except Exception:
                    pass

            if page.locator("#username").count() > 0 and page.locator("#password").count() > 0:
                if not tonies_email or not tonies_password:
                    return
                page.fill("#username", tonies_email, timeout=30000)
                page.fill("#password", tonies_password, timeout=30000)
                for sel in ["button:has-text('Continue')", "button[type='submit']"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible(timeout=1000):
                            loc.click()
                            break
                    except Exception:
                        pass
                page.wait_for_load_state("networkidle")
                page.goto(creative_url, wait_until="domcontentloaded")
                page.wait_for_load_state("networkidle")

        do_login_if_present()

        # Dynamic cards can render after networkidle; wait and retry once.
        try:
            page.wait_for_selector("a[href*='/creative-tonies/']", timeout=5000)
        except Exception:
            page.wait_for_timeout(1800)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            do_login_if_present()
            page.wait_for_timeout(1500)

        cards = page.eval_on_selector_all(
            "a[href*='/creative-tonies/']",
            "els => els.map(e => ({href:e.href, text:(e.innerText||'').trim(), img:(e.querySelector('img')?.src||'')}))"
        )

        by_href: dict[str, dict] = {}
        for c in cards:
            href = (c.get("href") or "").split("?")[0].rstrip('/')
            if "/creative-tonies/" not in href:
                continue
            cur = by_href.get(href, {"href": href, "text": "", "img": ""})
            txt = (c.get("text") or "").strip()
            img = (c.get("img") or "").strip()
            # Prefer non-empty text/img encountered later (some duplicate anchors are wrappers).
            if txt and (not cur.get("text") or len(txt) > len(cur.get("text") or "")):
                cur["text"] = txt
            if img and not cur.get("img"):
                cur["img"] = img
            by_href[href] = cur

        out = []
        for h, c in by_href.items():
            parts = h.split('/creative-tonies/')
            if len(parts) < 2:
                continue
            tail = parts[1].strip('/')
            segs = tail.split('/')
            if len(segs) < 2:
                continue

            edit_url = f"{base_url}/creative-tonies/{segs[0]}/{segs[1]}/edit?withUpload=true"
            name = (c.get("text") or "").split('\n')[0].strip() or f"Creative Tonies {segs[1]}"
            out.append({
                "name": name,
                "image": c.get("img") or "",
                "edit_url": edit_url,
            })

        try:
            settings.tonies_storage_state_file.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(settings.tonies_storage_state_file))
        except Exception:
            pass

        context.close()
        browser.close()
        return out
