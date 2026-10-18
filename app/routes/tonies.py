"""Creative Tonie reads, queued actions, undo, and direct content-edit routes."""
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..tonies_api import parse_target_url
from ..uploader import (
    action_queue,
    delete_all_tonies_content,
    delete_tonies_chapter,
    get_tonies_content,
    list_creative_tonies,
    rename_tonies_chapter,
    reorder_tonies_chapter,
)

router = APIRouter()
# Keep the pre-router logger name so log output stays unchanged.
logger = logging.getLogger("app.main")


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


@router.get('/api/creative-tonies')
def creative_tonies():
    try:
        return list_creative_tonies()
    except Exception:
        return {"error": "Could not load Tonies; check the account connection and retry"}


@router.post('/api/tonies-actions')
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


@router.post('/api/tonies-actions/{action_id}/undo')
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


@router.get('/api/tonies-actions')
def list_tonies_actions(target_url: str = None):
    return {'actions': action_queue.recent(target_url)}


@router.post('/api/tonies-content')
def tonies_content(req: ToniesContentRequest):
    try:
        return get_tonies_content(req.target_url)
    except Exception:
        return {"error": "Could not load Tonie content; check the account connection and refresh"}


@router.post('/api/tonies-content/delete')
def tonies_content_delete(req: ToniesDeleteRequest):
    try:
        return delete_tonies_chapter(req.target_url, req.index, req.chapter_title, req.chapter_id, req.chapter_fingerprint, req.app_track_token)
    except Exception:
        logger.warning("Tonies delete failed; outcome unconfirmed; refresh before another action")
        return {"error": "Delete outcome unconfirmed; refresh before another action"}


@router.post('/api/tonies-content/delete-all')
def tonies_content_delete_all(req: ToniesContentRequest):
    try:
        return delete_all_tonies_content(req.target_url)
    except Exception:
        logger.warning("Tonies delete-all failed; outcome unconfirmed; refresh before another action")
        return {"error": "Delete outcome unconfirmed; refresh before another action"}


@router.post('/api/tonies-content/reorder')
def tonies_content_reorder(req: ToniesReorderRequest):
    try:
        return reorder_tonies_chapter(req.target_url, req.from_index, req.to_index, chapter_id=req.chapter_id)
    except Exception:
        return {"error": "Reorder outcome unconfirmed; refresh before another action"}


@router.post('/api/tonies-content/rename')
def tonies_content_rename(req: ToniesRenameRequest):
    try:
        return rename_tonies_chapter(req.target_url, req.index, req.title, chapter_id=req.chapter_id)
    except Exception:
        return {"error": "Rename outcome unconfirmed; refresh before another action"}
