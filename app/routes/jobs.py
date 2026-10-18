"""Search jobs, candidate selection, downloads, and cancellation HTTP routes."""
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StrictInt

from ..downloader import PLAYLIST_MAX, clear_search_cache
from ..job_pipeline import fetch_candidate, select_candidate_and_continue, select_candidates_and_continue
from ..job_store import cancel_job, create_job, get_job, list_jobs
from ..models import ChatRequest
from ..tonies_batch import clean_position

router = APIRouter()


class SelectionRequest(BaseModel):
    index: int
    target_url: str = None
    position: Optional[dict] = None


class FetchRequest(BaseModel):
    index: StrictInt


class MultiSelectionRequest(BaseModel):
    indexes: list[StrictInt] = Field(min_length=1, max_length=PLAYLIST_MAX)
    target_url: str = None
    position: Optional[dict] = None


@router.post("/api/chat")
def submit_chat(req: ChatRequest):
    limit = int(req.search_limit or 5)
    if limit not in (5, 10, 15, 20):
        limit = 5
    job = create_job(req.message, search_limit=limit)
    return {"job_id": job.id, "status": job.status}


@router.get("/api/jobs")
def jobs():
    return [j.model_dump() for j in list_jobs()]


@router.post('/api/search-cache/clear')
def api_clear_search_cache():
    clear_search_cache()
    return {'ok': True}


@router.post("/api/jobs/{job_id}/select")
def select_candidate(job_id: str, req: SelectionRequest):
    try:
        selected_job_id = select_candidate_and_continue(job_id, req.index, req.target_url, clean_position(req.position))
        j = get_job(selected_job_id)
        return j.model_dump() if j else {"error": "not_found"}
    except Exception:
        return {"error": "Could not select this candidate; refresh and check the selection"}


@router.post("/api/jobs/{job_id}/fetch")
async def fetch_candidate_to_library(job_id: str, req: FetchRequest):
    # Awaited, not blocked on: a long queue of downloads must not tie up a server thread.
    try:
        path = await asyncio.wrap_future(fetch_candidate(job_id, req.index))
        return {"filename": Path(path).name}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except RuntimeError:
        raise HTTPException(status_code=502, detail="Could not download this video; try again") from None


@router.post("/api/jobs/{job_id}/select-many")
def select_candidates(job_id: str, req: MultiSelectionRequest):
    try:
        position = clean_position(req.position)
        return {"job_ids": select_candidates_and_continue(job_id, req.indexes, req.target_url, position)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job_api(job_id: str):
    try:
        j = cancel_job(job_id)
        return j.model_dump() if j else {"error": "not_found"}
    except Exception:
        return {"error": "Could not cancel this job; refresh to check its current status"}


@router.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = get_job(job_id)
    if not j:
        return {"error": "not_found"}
    return j.model_dump()
