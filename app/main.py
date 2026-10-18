import json
import logging
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, auth
from .config import settings
from .credentials import setup_status, initialize_vault, login_unlock, lock_runtime, get_credentials, change_app_password, update_tonies_credentials, vault_configured, VaultError
from .media_files import library_file, library_files, store_upload
from .media_process import Trim
from .downloader import clear_search_cache
from .jobs import create_job, get_job, list_jobs, start_worker, select_candidate_and_continue, select_candidates_and_continue, create_upload_only_job, get_events_since, get_event_stats, get_queue_stats, cancel_job
from .logger import setup_logging
from .models import ChatRequest
from .subprocess_runner import run_bounded
from .web_pages import page_response
from .tonies_api import parse_target_url
from .tonies_batch import clean_position
from .uploader import action_queue, list_creative_tonies, get_tonies_content, delete_tonies_chapter, delete_all_tonies_content, reorder_tonies_chapter, rename_tonies_chapter, reset_upload_session

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Starting Tonies-YT %s | log_file=%s | log_level=%s", __version__, settings.log_file, settings.log_level)
    start_worker()
    yield


app = FastAPI(title="Tonies-YT", version=__version__, lifespan=lifespan)
app.add_middleware(auth.SessionGuard, is_configured=vault_configured)

app.mount("/static", StaticFiles(directory="web"), name="static")


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _error: RequestValidationError):
    # Default errors echo request values, including passwords and signed URLs.
    return JSONResponse({"detail": "Invalid request fields or JSON body"}, status_code=422)

_sse_stats = {
    "active_connections": 0,
    "total_connections": 0,
    "disconnects": 0,
}


def _nocache_file(path: str):
    return page_response(path, version=__version__)


@app.get('/favicon.ico', include_in_schema=False)
def favicon():
    return FileResponse('web/favicon.ico', media_type='image/x-icon')


class Health(BaseModel):
    status: str


class TrimRequest(BaseModel):
    start: float = Field(default=0, ge=0, le=86400)
    end: Optional[float] = Field(default=None, gt=0, le=86400)


class SelectionRequest(BaseModel):
    index: int
    target_url: str = None
    position: Optional[dict] = None
    trim: Optional[TrimRequest] = None


class MultiSelectionRequest(BaseModel):
    indexes: list[int]
    target_url: str = None
    position: Optional[dict] = None


class UploadExistingRequest(BaseModel):
    filename: str
    target_url: str = None
    position: Optional[dict] = None


class ToniesActionsRequest(BaseModel):
    target_url: str
    actions: list[dict]


class ToniesContentRequest(BaseModel):
    target_url: str


class ToniesDeleteRequest(BaseModel):
    target_url: str
    index: int
    chapter_title: str = ""
    chapter_id: str = ""
    chapter_fingerprint: str = ""
    app_track_token: str = ""


class ToniesReorderRequest(BaseModel):
    target_url: str
    from_index: int
    to_index: int
    chapter_id: str | None = None


class ToniesRenameRequest(BaseModel):
    target_url: str
    index: int
    title: str
    chapter_id: str | None = None


class SetupInitRequest(BaseModel):
    username: str = Field(max_length=256)
    app_password: str = Field(max_length=1024)
    tonies_email: str = Field(default="", max_length=320)
    tonies_password: str = Field(default="", max_length=1024)


class SetupLoginRequest(BaseModel):
    username: str = Field(max_length=256)
    app_password: str  # Legacy passwords are admitted within the total account-body cap.


class SetupChangePasswordRequest(BaseModel):
    username: str = Field(max_length=256)
    current_password: str
    new_password: str = Field(max_length=1024)
    creative_upload_url: str = Field(default="", max_length=2048)


class SetupToniesCredentialsRequest(BaseModel):
    username: str = Field(max_length=256)
    app_password: str
    tonies_email: str = Field(default="", max_length=320)
    tonies_password: str = Field(default="", max_length=1024)




@app.get("/api/health", response_model=Health)
def health():
    return Health(status="ok")


VERSION_PROBE_TIMEOUT_SEC = 12
VERSION_PROBE_OUTPUT_BYTES = 4096


def _cmd_version(cmd: list[str]) -> str:
    """First stdout line of a tool's --version; stderr is never read or shown."""
    try:
        output = run_bounded(cmd, operation="Version probe", timeout=VERSION_PROBE_TIMEOUT_SEC,
                             max_output_bytes=VERSION_PROBE_OUTPUT_BYTES)
    except RuntimeError:
        return "unknown"
    lines = output.decode("utf-8", "replace").strip().splitlines()
    return lines[0].strip()[:80] if lines else "unknown"


@app.get('/api/setup/status')
def api_setup_status(request: Request):
    s = setup_status()
    if not auth.request_session(request):
        # Before sign-in, only say whether setup or sign-in comes next.
        return {"configured": s["configured"], "unlocked": False, "signed_in": False, "source": s["source"]}
    c = get_credentials()
    email = (c.get("email") or "").strip()
    return {
        **s,
        "signed_in": True,
        "app_version": __version__,
        "email_hint": (email[:2] + "***") if email else "",
        "tonies_email": email if s.get("unlocked") else "",
        "versions": {
            "yt_dlp": _cmd_version(["yt-dlp", "--version"]),
            "deno": _cmd_version(["deno", "--version"]),
        },
    }


def _signed_in_response(request: Request, body: dict, *, generation: int) -> JSONResponse:
    token = auth.create_session(expected_generation=generation)
    if token is None:
        return JSONResponse({"ok": False, "error": "Account state changed during sign-in; please sign in again"},
                            status_code=409)
    response = JSONResponse(body)
    auth.set_session_cookie(response, request, token)
    return response


def _account_result(operation, *args) -> dict:
    """Only deliberately authored vault errors cross the HTTP boundary."""
    try:
        return operation(*args)
    except VaultError as error:
        return {"ok": False, "error": str(error)}
    except Exception:
        return {"ok": False, "error": "Vault storage is unavailable; check permissions or restore a known-good backup"}


def _password_busy() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "Another sign-in or setup is in progress; try again shortly"},
                        status_code=429, headers={"Retry-After": "1"})


@app.post('/api/setup/init')
def api_setup_init(req: SetupInitRequest, request: Request):
    with auth.password_attempt() as allowed:
        if not allowed:
            return _password_busy()
        with auth.account_transition():
            generation = auth.session_generation()
            out = _account_result(initialize_vault, req.username, req.app_password, req.tonies_email, req.tonies_password)
    if not out.get("ok"):
        return out
    reset_upload_session()
    return _signed_in_response(request, {"ok": True, **out}, generation=generation)


@app.post('/api/setup/login')
def api_setup_login(req: SetupLoginRequest, request: Request):
    client = request.client.host if request.client else "unknown"
    wait = auth.login_retry_after(client)
    if wait:
        return JSONResponse({"ok": False, "error": f"Too many failed sign-ins; try again in {wait} s"},
                            status_code=429, headers={"Retry-After": str(wait)})
    with auth.password_attempt() as allowed:
        if not allowed:
            return _password_busy()
        with auth.account_transition():
            generation = auth.session_generation()
            result = _account_result(login_unlock, req.username, req.app_password)
    if not result.get('ok'):
        auth.record_login_failure(client)
        return result
    auth.clear_login_failures(client)
    reset_upload_session()
    return _signed_in_response(request, result, generation=generation)


@app.post('/api/setup/lock')
def api_setup_lock():
    """Lock the vault and sign every browser out."""
    with auth.account_transition():
        result = lock_runtime()
        auth.revoke_all_sessions()
    reset_upload_session()
    response = JSONResponse(result)
    auth.clear_session_cookie(response)
    return response


@app.post('/api/setup/change-password')
def api_setup_change_password(req: SetupChangePasswordRequest, request: Request):
    with auth.account_transition():
        result = _account_result(change_app_password, req.username, req.current_password, req.new_password)
        if result.get('ok'):
            auth.revoke_all_sessions(keep=auth.request_session(request))
    if result.get('ok'):
        reset_upload_session()
    return result


@app.post('/api/setup/update-tonies-credentials')
def api_setup_update_tonies_credentials(req: SetupToniesCredentialsRequest, request: Request):
    with auth.account_transition():
        result = _account_result(update_tonies_credentials, req.username, req.app_password, req.tonies_email, req.tonies_password)
        if result.get('ok'):
            auth.revoke_all_sessions(keep=auth.request_session(request))
    if result.get('ok'):
        reset_upload_session()
    return result


@app.post('/api/setup/update-yt-dlp')
def api_setup_update_ytdlp():
    # Kept so 2.0 pages still open in a browser get a clear answer instead of a 404.
    return {"ok": False, "update_supported": False,
            "error": "Runtime updates are disabled. Rebuild or pull an updated container image to update yt-dlp"}


@app.post("/api/chat")
def submit_chat(req: ChatRequest):
    limit = int(req.search_limit or 5)
    if limit not in (5, 10, 15, 20):
        limit = 5
    job = create_job(req.message, search_limit=limit)
    return {"job_id": job.id, "status": job.status}


@app.get("/api/jobs")
def jobs():
    return [j.model_dump() for j in list_jobs()]


@app.get('/api/events')
def events(request: Request, lastEventId: int = 0):
    last_id = int(lastEventId or 0)
    session = auth.request_session(request)
    _sse_stats["total_connections"] += 1
    _sse_stats["active_connections"] += 1
    logger.info("sse connect active=%s total=%s lastEventId=%s", _sse_stats["active_connections"], _sse_stats["total_connections"], last_id)

    def gen():
        nonlocal last_id
        try:
            while auth.session_valid(session):
                events_batch = get_events_since(last_id, timeout_sec=15.0)
                if not events_batch:
                    yield ": keepalive\n\n"
                    continue

                for evt in events_batch:
                    last_id = max(last_id, int(evt.get("id", 0)))
                    # Unnamed events reach EventSource.onmessage in every app page.
                    yield f"id: {last_id}\n"
                    yield f"data: {json.dumps(evt)}\n\n"
        except GeneratorExit:
            pass
        finally:
            _sse_stats["active_connections"] = max(0, _sse_stats["active_connections"] - 1)
            _sse_stats["disconnects"] += 1
            logger.info("sse disconnect active=%s disconnects=%s", _sse_stats["active_connections"], _sse_stats["disconnects"])

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


@app.get('/api/events/stats')
def events_stats():
    return {
        "sse": dict(_sse_stats),
        "jobs": get_event_stats(),
        "queues": get_queue_stats(),
    }


@app.get('/api/creative-tonies')
def creative_tonies():
    try:
        return list_creative_tonies()
    except Exception:
        return {"error": "Could not load Tonies; check the account connection and retry"}


@app.get('/api/files')
def files():
    return library_files()


@app.post('/api/upload-existing')
def upload_existing(req: UploadExistingRequest):
    if Path(req.filename).name != req.filename or Path(req.filename).suffix.lower() != '.mp3':
        raise HTTPException(status_code=400, detail='Invalid library filename')
    p = library_file(req.filename)
    try:
        position = clean_position(req.position)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    job = create_upload_only_job(str(p), note=f'upload existing: {req.filename}', target_url=req.target_url,
                                 position=position)
    return {'job_id': job.id, 'status': job.status}


@app.post('/api/tonies-actions')
def submit_tonies_actions(req: ToniesActionsRequest):
    """Queue library edits for one Tonie. They are batched and applied in order; progress
    and the resulting library arrive as `tonies_actions` events on /api/events."""
    try:
        parse_target_url(req.target_url)
        if not 1 <= len(req.actions) <= 200:
            raise ValueError("Send between 1 and 200 actions")
        actions = action_queue.submit(req.target_url, req.actions)
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=400, detail="Action request refused; refresh the Tonie and check the selected actions") from None
    return {'actions': [a.public() for a in actions]}


@app.post('/api/tonies-actions/{action_id}/undo')
def undo_tonies_actions(action_id: str):
    """Undo one server-owned submission, or cancel its still-pending deletion."""
    try:
        actions = action_queue.undo(action_id)
    except (ValueError, RuntimeError):
        return JSONResponse({
            "detail": "Undo unavailable or outcome unconfirmed; refresh the Tonie before another action",
            "needs_refresh": True,
        }, status_code=409)
    result: dict = {'actions': [action.public() for action in actions]}
    if actions and actions[0].content is not None:
        result['content'] = actions[0].content
    return result


@app.get('/api/tonies-actions')
def list_tonies_actions(target_url: str = None):
    return {'actions': action_queue.recent(target_url)}


@app.post('/api/search-cache/clear')
def api_clear_search_cache():
    clear_search_cache()
    return {'ok': True}


@app.post('/api/files/upload')
async def upload_file_to_library(file: UploadFile = File(...)):
    return await store_upload(file)


@app.post('/api/tonies-content')
def tonies_content(req: ToniesContentRequest):
    try:
        return get_tonies_content(req.target_url)
    except Exception:
        return {"error": "Could not load Tonie content; check the account connection and refresh"}


@app.post('/api/tonies-content/delete')
def tonies_content_delete(req: ToniesDeleteRequest):
    try:
        return delete_tonies_chapter(req.target_url, req.index, req.chapter_title, req.chapter_id, req.chapter_fingerprint, req.app_track_token)
    except Exception:
        logger.warning("Tonies delete failed; outcome unconfirmed; refresh before another action")
        return {"error": "Delete outcome unconfirmed; refresh before another action"}


@app.post('/api/tonies-content/delete-all')
def tonies_content_delete_all(req: ToniesContentRequest):
    try:
        return delete_all_tonies_content(req.target_url)
    except Exception:
        logger.warning("Tonies delete-all failed; outcome unconfirmed; refresh before another action")
        return {"error": "Delete outcome unconfirmed; refresh before another action"}


@app.post('/api/tonies-content/reorder')
def tonies_content_reorder(req: ToniesReorderRequest):
    try:
        return reorder_tonies_chapter(req.target_url, req.from_index, req.to_index, chapter_id=req.chapter_id)
    except Exception:
        return {"error": "Reorder outcome unconfirmed; refresh before another action"}


@app.post('/api/tonies-content/rename')
def tonies_content_rename(req: ToniesRenameRequest):
    try:
        return rename_tonies_chapter(req.target_url, req.index, req.title, chapter_id=req.chapter_id)
    except Exception:
        return {"error": "Rename outcome unconfirmed; refresh before another action"}


@app.get('/api/files/{filename}')
def download_file(filename: str, inline: bool = False):
    p = library_file(filename)
    return FileResponse(
        str(p), filename=p.name, media_type='audio/mpeg',
        content_disposition_type='inline' if inline else 'attachment',
        headers={'Cache-Control': 'no-store'} if inline else None,
    )


@app.delete('/api/files/{filename}')
def delete_file(filename: str):
    p = library_file(filename)
    p.unlink()
    return {'ok': True, 'deleted': filename}


@app.post("/api/jobs/{job_id}/select")
def select_candidate(job_id: str, req: SelectionRequest):
    trim = None
    if req.trim is not None:
        try:
            trim = Trim(start=req.trim.start, end=req.trim.end).validate()
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
    try:
        selected_job_id = select_candidate_and_continue(job_id, req.index, req.target_url,
                                                        clean_position(req.position), trim=trim)
        j = get_job(selected_job_id)
        return j.model_dump() if j else {"error": "not_found"}
    except Exception:
        return {"error": "Could not select this candidate; refresh and check the selection"}


@app.post("/api/jobs/{job_id}/select-many")
def select_candidates(job_id: str, req: MultiSelectionRequest):
    try:
        position = clean_position(req.position)
        return {"job_ids": select_candidates_and_continue(job_id, req.indexes, req.target_url, position)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job_api(job_id: str):
    try:
        j = cancel_job(job_id)
        return j.model_dump() if j else {"error": "not_found"}
    except Exception:
        return {"error": "Could not cancel this job; refresh to check its current status"}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = get_job(job_id)
    if not j:
        return {"error": "not_found"}
    return j.model_dump()


@app.get('/api/logs')
def api_logs(lines: int = 300):
    lines = max(10, min(lines, 2000))
    if not settings.log_file.exists():
        return {"lines": [], "file": str(settings.log_file), "level": settings.log_level}

    with settings.log_file.open('r', encoding='utf-8', errors='replace') as f:
        tail = list(deque(f, maxlen=lines))

    return {
        "lines": [x.rstrip('\n') for x in tail],
        "file": str(settings.log_file),
        "level": settings.log_level,
    }


@app.get('/api/logs/export')
def api_logs_export(lines: int = 2000):
    lines = max(10, min(lines, 10000))
    if not settings.log_file.exists():
        content = ""
    else:
        with settings.log_file.open('r', encoding='utf-8', errors='replace') as f:
            tail = list(deque(f, maxlen=lines))
        content = "".join(tail)

    headers = {
        "Content-Disposition": 'attachment; filename="tonies-yt-logs.txt"'
    }
    return PlainTextResponse(content=content, headers=headers)


@app.get('/logs')
def logs_page():
    return _nocache_file('web/logs.html')


@app.get('/setup')
def setup_page(request: Request):
    if vault_configured():
        return RedirectResponse(url='/' if auth.request_session(request) else '/login', status_code=302)
    return _nocache_file('web/setup.html')


@app.get('/login')
def login_page(request: Request):
    if not vault_configured():
        return RedirectResponse(url='/setup', status_code=302)
    if auth.request_session(request) and setup_status().get('unlocked'):
        return RedirectResponse(url='/', status_code=302)
    return _nocache_file('web/login.html')


@app.get('/settings')
def settings_page():
    return _nocache_file('web/account.html')


@app.get('/account')
def account_page_redirect():
    return RedirectResponse(url='/settings', status_code=302)


@app.get("/")
def index():
    # SessionGuard has already sent visitors without a session to /setup or /login.
    if not setup_status().get('unlocked'):
        return RedirectResponse(url='/login', status_code=302)
    return _nocache_file("web/index.html")
