import json
import logging
import re
import subprocess
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, auth
from .config import settings
from .credentials import setup_status, initialize_vault, login_unlock, lock_runtime, get_credentials, change_app_password, update_tonies_credentials, vault_configured
from .downloader import sanitize_filename, clear_search_cache
from .jobs import create_job, get_job, list_jobs, start_worker, select_candidate_and_continue, create_upload_only_job, get_events_since, get_event_stats, get_queue_stats, cancel_job
from .logger import setup_logging
from .models import ChatRequest
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

_sse_stats = {
    "active_connections": 0,
    "total_connections": 0,
    "disconnects": 0,
}


def _nocache_file(path: str):
    return FileResponse(path, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    })


@app.get('/favicon.ico', include_in_schema=False)
def favicon():
    return FileResponse('web/favicon.ico', media_type='image/x-icon')


class Health(BaseModel):
    status: str


class SelectionRequest(BaseModel):
    index: int
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
    username: str
    app_password: str
    tonies_email: str = ""  # optional when TONIES_EMAIL/TONIES_PASSWORD are set
    tonies_password: str = ""


class SetupLoginRequest(BaseModel):
    username: str
    app_password: str


class SetupChangePasswordRequest(BaseModel):
    username: str
    current_password: str
    new_password: str
    creative_upload_url: str = ""


class SetupToniesCredentialsRequest(BaseModel):
    username: str
    app_password: str
    tonies_email: str = ""
    tonies_password: str = ""




@app.get("/api/health", response_model=Health)
def health():
    return Health(status="ok")


def _cmd_version(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        txt = (out.stdout or out.stderr or "").strip().splitlines()
        return txt[0].strip() if txt else "unknown"
    except Exception:
        return "unknown"


def _yt_dlp_latest_version() -> str:
    """Best-effort latest stable version probe without changing install state."""
    try:
        out = subprocess.run(["yt-dlp", "-U"], capture_output=True, text=True, timeout=25)
        blob = ((out.stdout or "") + "\n" + (out.stderr or "")).strip()
        m = re.search(r"Latest version:\s*stable@([0-9]{4}\.[0-9]{2}\.[0-9]{2})", blob)
        if m:
            return m.group(1)
        m2 = re.search(r"stable@([0-9]{4}\.[0-9]{2}\.[0-9]{2})", blob)
        if m2:
            return m2.group(1)
        return "unknown"
    except Exception:
        return "unknown"


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
            "yt_dlp_latest": _yt_dlp_latest_version(),
            "deno": _cmd_version(["deno", "--version"]),
        },
    }


def _signed_in_response(request: Request, body: dict) -> JSONResponse:
    response = JSONResponse(body)
    auth.set_session_cookie(response, request, auth.create_session())
    return response


@app.post('/api/setup/init')
def api_setup_init(req: SetupInitRequest, request: Request):
    try:
        out = initialize_vault(req.username, req.app_password, req.tonies_email, req.tonies_password)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    reset_upload_session()
    return _signed_in_response(request, {"ok": True, **out})


@app.post('/api/setup/login')
def api_setup_login(req: SetupLoginRequest, request: Request):
    client = request.client.host if request.client else "unknown"
    wait = auth.login_retry_after(client)
    if wait:
        return JSONResponse({"ok": False, "error": f"Too many failed sign-ins; try again in {wait} s"},
                            status_code=429, headers={"Retry-After": str(wait)})
    result = login_unlock(req.username, req.app_password)
    if not result.get('ok'):
        auth.record_login_failure(client)
        return result
    auth.clear_login_failures(client)
    reset_upload_session()
    return _signed_in_response(request, result)


@app.post('/api/setup/lock')
def api_setup_lock():
    """Lock the vault and sign every browser out."""
    result = lock_runtime()
    auth.revoke_all_sessions()
    reset_upload_session()
    response = JSONResponse(result)
    auth.clear_session_cookie(response)
    return response


@app.post('/api/setup/change-password')
def api_setup_change_password(req: SetupChangePasswordRequest, request: Request):
    result = change_app_password(req.username, req.current_password, req.new_password)
    if result.get('ok'):
        auth.revoke_all_sessions(keep=auth.request_session(request))
    return result


@app.post('/api/setup/update-tonies-credentials')
def api_setup_update_tonies_credentials(req: SetupToniesCredentialsRequest):
    result = update_tonies_credentials(req.username, req.app_password, req.tonies_email, req.tonies_password)
    if result.get('ok'):
        reset_upload_session()
    return result


@app.post('/api/setup/update-yt-dlp')
def api_setup_update_ytdlp():
    try:
        before = _cmd_version(["yt-dlp", "--version"])
        cmd = ["python", "-m", "pip", "install", "--no-cache-dir", "--upgrade", "yt-dlp"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        after = _cmd_version(["yt-dlp", "--version"])
        ok = out.returncode == 0
        return {
            "ok": ok,
            "before": before,
            "after": after,
            "output": ((out.stdout or "") + "\n" + (out.stderr or "")).strip()[-2000:],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
                    # Unnamed events, so the page's EventSource.onmessage receives them.
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
    except Exception as e:
        return {"error": str(e)}


@app.get('/api/files')
def files():
    def probe_duration_sec(path: str) -> float:
        try:
            cmd = [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'json',
                path,
            ]
            out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            data = json.loads(out.stdout or '{}')
            dur = data.get('format', {}).get('duration')
            return float(dur) if dur is not None else None
        except Exception:
            return None

    downloads = settings.data_dir / 'downloads'
    items = []
    for p in sorted(downloads.glob('*.mp3'), key=lambda x: x.stat().st_mtime, reverse=True):
        items.append({
            'name': p.name,
            'path': str(p),
            'size_bytes': p.stat().st_size,
            'duration_sec': probe_duration_sec(str(p)),
            'modified': p.stat().st_mtime,
        })
    return items


@app.post('/api/upload-existing')
def upload_existing(req: UploadExistingRequest):
    if Path(req.filename).name != req.filename or Path(req.filename).suffix.lower() != '.mp3':
        raise HTTPException(status_code=400, detail='Invalid library filename')
    p = settings.data_dir / 'downloads' / req.filename
    if not p.is_file():
        raise HTTPException(status_code=404, detail='Library file not found')
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
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {'actions': [a.public() for a in actions]}


@app.get('/api/tonies-actions')
def list_tonies_actions(target_url: str = None):
    return {'actions': action_queue.recent(target_url)}


@app.post('/api/search-cache/clear')
def api_clear_search_cache():
    clear_search_cache()
    return {'ok': True}


@app.post('/api/files/upload')
async def upload_file_to_library(file: UploadFile = File(...)):
    uploads_dir = settings.data_dir / 'uploads'
    downloads_dir = settings.data_dir / 'downloads'
    uploads_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    raw_name = Path(file.filename or 'upload').stem
    safe_base = sanitize_filename(raw_name)
    suffix = Path(file.filename or '').suffix or '.bin'
    temp_path = uploads_dir / f"{safe_base}{suffix}"

    blob = await file.read()
    temp_path.write_bytes(blob)

    out_path = downloads_dir / f"{safe_base}.mp3"
    if out_path.exists():
        out_path = downloads_dir / f"{safe_base}_{abs(hash(blob)) % 100000}.mp3"

    cmd = [
        'ffmpeg', '-y',
        '-i', str(temp_path),
        '-vn',
        '-ar', '44100',
        '-ac', '2',
        '-codec:a', 'libmp3lame',
        '-b:a', '128k',
        str(out_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=240)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass

    return {'ok': True, 'filename': out_path.name}


@app.post('/api/tonies-content')
def tonies_content(req: ToniesContentRequest):
    try:
        return get_tonies_content(req.target_url)
    except Exception as e:
        return {"error": str(e)}


@app.post('/api/tonies-content/delete')
def tonies_content_delete(req: ToniesDeleteRequest):
    try:
        return delete_tonies_chapter(req.target_url, req.index, req.chapter_title, req.chapter_id, req.chapter_fingerprint, req.app_track_token)
    except Exception as e:
        logger.exception("tonies delete failed target_url=%s index=%s chapter_title=%s chapter_id=%s chapter_fingerprint=%s app_track_token=%s", req.target_url, req.index, req.chapter_title, req.chapter_id, req.chapter_fingerprint, req.app_track_token)
        return {"error": str(e)}


@app.post('/api/tonies-content/delete-all')
def tonies_content_delete_all(req: ToniesContentRequest):
    try:
        return delete_all_tonies_content(req.target_url)
    except Exception as e:
        logger.exception("tonies delete-all failed target_url=%s", req.target_url)
        return {"error": str(e)}


@app.post('/api/tonies-content/reorder')
def tonies_content_reorder(req: ToniesReorderRequest):
    try:
        return reorder_tonies_chapter(req.target_url, req.from_index, req.to_index, chapter_id=req.chapter_id)
    except Exception as e:
        return {"error": str(e)}


@app.post('/api/tonies-content/rename')
def tonies_content_rename(req: ToniesRenameRequest):
    try:
        return rename_tonies_chapter(req.target_url, req.index, req.title, chapter_id=req.chapter_id)
    except Exception as e:
        return {"error": str(e)}


@app.get('/api/files/{filename}')
def download_file(filename: str):
    p = settings.data_dir / 'downloads' / filename
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail='not_found')
    return FileResponse(str(p), filename=p.name, media_type='audio/mpeg')


@app.delete('/api/files/{filename}')
def delete_file(filename: str):
    p = settings.data_dir / 'downloads' / filename
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail='not_found')
    p.unlink()
    return {'ok': True, 'deleted': filename}


@app.post("/api/jobs/{job_id}/select")
def select_candidate(job_id: str, req: SelectionRequest):
    try:
        selected_job_id = select_candidate_and_continue(job_id, req.index, req.target_url, clean_position(req.position))
        j = get_job(selected_job_id)
        return j.model_dump() if j else {"error": "not_found"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job_api(job_id: str):
    try:
        j = cancel_job(job_id)
        return j.model_dump() if j else {"error": "not_found"}
    except Exception as e:
        return {"error": str(e)}


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
