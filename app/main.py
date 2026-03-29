from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import subprocess
import json
import logging
import re
from pathlib import Path
from collections import deque

from .jobs import create_job, get_job, list_jobs, start_worker, select_candidate_and_continue, create_upload_only_job, get_events_since, get_event_stats, get_queue_stats, cancel_job
from .models import ChatRequest
from .config import settings
from .uploader import list_creative_tonies, get_tonies_content, delete_tonies_chapter, delete_all_tonies_content, reorder_tonies_chapter, rename_tonies_chapter
from .logger import setup_logging
from .credentials import setup_status, initialize_vault, login_unlock, lock_runtime, get_credentials, change_app_password, update_tonies_credentials
from .downloader import sanitize_filename, clear_search_cache

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="tonies-auto")

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


class Health(BaseModel):
    status: str


class SelectionRequest(BaseModel):
    index: int
    target_url: str = None


class UploadExistingRequest(BaseModel):
    filename: str
    target_url: str = None


class ToniesContentRequest(BaseModel):
    target_url: str


class ToniesDeleteRequest(BaseModel):
    target_url: str
    index: int
    chapter_title: str = ""
    chapter_occurrence: int = 0
    chapter_id: str = ""
    chapter_fingerprint: str = ""
    app_track_token: str = ""


class ToniesReorderRequest(BaseModel):
    target_url: str
    from_index: int
    to_index: int


class ToniesRenameRequest(BaseModel):
    target_url: str
    index: int
    title: str


class SetupInitRequest(BaseModel):
    username: str
    app_password: str
    tonies_email: str
    tonies_password: str


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


@app.on_event("startup")
def on_startup():
    logger.info("Starting tonies-auto | log_file=%s | log_level=%s", settings.log_file, settings.log_level)
    start_worker()


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
def api_setup_status():
    s = setup_status()
    c = get_credentials()
    email = (c.get("email") or "").strip()
    return {
        **s,
        "email_hint": (email[:2] + "***") if email else "",
        "tonies_email": email if s.get("unlocked") else "",
        "versions": {
            "yt_dlp": _cmd_version(["yt-dlp", "--version"]),
            "yt_dlp_latest": _yt_dlp_latest_version(),
            "deno": _cmd_version(["deno", "--version"]),
        },
    }


@app.post('/api/setup/init')
def api_setup_init(req: SetupInitRequest):
    try:
        out = initialize_vault(req.username, req.app_password, req.tonies_email, req.tonies_password)
        return {"ok": True, **out}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post('/api/setup/login')
def api_setup_login(req: SetupLoginRequest):
    return login_unlock(req.username, req.app_password)


@app.post('/api/setup/lock')
def api_setup_lock():
    return lock_runtime()


@app.post('/api/setup/change-password')
def api_setup_change_password(req: SetupChangePasswordRequest):
    return change_app_password(req.username, req.current_password, req.new_password)


@app.post('/api/setup/update-tonies-credentials')
def api_setup_update_tonies_credentials(req: SetupToniesCredentialsRequest):
    return update_tonies_credentials(req.username, req.app_password, req.tonies_email, req.tonies_password)


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
def events(lastEventId: int = 0):
    last_id = int(lastEventId or 0)
    _sse_stats["total_connections"] += 1
    _sse_stats["active_connections"] += 1
    logger.info("sse connect active=%s total=%s lastEventId=%s", _sse_stats["active_connections"], _sse_stats["total_connections"], last_id)

    def gen():
        nonlocal last_id
        try:
            while True:
                events_batch = get_events_since(last_id, timeout_sec=15.0)
                if not events_batch:
                    yield ": keepalive\n\n"
                    continue

                for evt in events_batch:
                    last_id = max(last_id, int(evt.get("id", 0)))
                    yield f"id: {last_id}\n"
                    yield "event: job\n"
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
    p = settings.data_dir / 'downloads' / req.filename
    job = create_upload_only_job(str(p), note=f'upload existing: {req.filename}', target_url=req.target_url)
    return {'job_id': job.id, 'status': job.status}


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
        return delete_tonies_chapter(req.target_url, req.index, req.chapter_title, req.chapter_occurrence, req.chapter_id, req.chapter_fingerprint, req.app_track_token)
    except Exception as e:
        logger.exception("tonies delete failed target_url=%s index=%s chapter_title=%s chapter_occurrence=%s chapter_id=%s chapter_fingerprint=%s app_track_token=%s", req.target_url, req.index, req.chapter_title, req.chapter_occurrence, req.chapter_id, req.chapter_fingerprint, req.app_track_token)
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
        return reorder_tonies_chapter(req.target_url, req.from_index, req.to_index)
    except Exception as e:
        return {"error": str(e)}


@app.post('/api/tonies-content/rename')
def tonies_content_rename(req: ToniesRenameRequest):
    try:
        return rename_tonies_chapter(req.target_url, req.index, req.title)
    except Exception as e:
        return {"error": str(e)}


@app.delete('/api/files/{filename}')
def delete_file(filename: str):
    p = settings.data_dir / 'downloads' / filename
    if not p.exists() or not p.is_file():
        return {'ok': False, 'error': 'not_found'}
    p.unlink()
    return {'ok': True, 'deleted': filename}


@app.post("/api/jobs/{job_id}/select")
def select_candidate(job_id: str, req: SelectionRequest):
    try:
        selected_job_id = select_candidate_and_continue(job_id, req.index, req.target_url)
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
        "Content-Disposition": 'attachment; filename="tonies-auto-logs.txt"'
    }
    return PlainTextResponse(content=content, headers=headers)


@app.get('/logs')
def logs_page():
    return _nocache_file('web/logs.html')


@app.get('/setup')
def setup_page():
    s = setup_status()
    if s.get('configured') and s.get('unlocked'):
        return RedirectResponse(url='/', status_code=302)
    if s.get('configured') and not s.get('unlocked'):
        return RedirectResponse(url='/login', status_code=302)
    return _nocache_file('web/setup.html')


@app.get('/login')
def login_page():
    s = setup_status()
    if not s.get('configured'):
        return RedirectResponse(url='/setup', status_code=302)
    if s.get('unlocked'):
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
    s = setup_status()
    if not s.get('configured'):
        return _nocache_file('web/setup.html')
    if not s.get('unlocked'):
        return _nocache_file('web/login.html')
    return _nocache_file("web/index.html")
