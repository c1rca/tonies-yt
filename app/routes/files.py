"""Local audio listing, upload, playback, waveform, clipping, and deletion routes."""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..job_upload import create_upload_only_job
from ..media_files import clip_library_file, library_file, library_files, library_waveform, store_upload
from ..tonies_batch import clean_position

router = APIRouter()


class ClipRequest(BaseModel):
    start: float = Field(ge=0, le=86400)
    end: float = Field(gt=0, le=86400)


class UploadExistingRequest(BaseModel):
    filename: str
    target_url: str = None
    position: Optional[dict] = None


@router.get('/api/files')
def files():
    return library_files()


@router.post('/api/upload-existing')
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


@router.post('/api/files/upload')
async def upload_file_to_library(file: UploadFile = File(...)):
    return await store_upload(file)


@router.get('/api/files/{filename}')
def download_file(filename: str, inline: bool = False):
    p = library_file(filename)
    return FileResponse(
        str(p), filename=p.name, media_type='audio/mpeg',
        content_disposition_type='inline' if inline else 'attachment',
        headers={'Cache-Control': 'no-store'} if inline else None,
    )


@router.get('/api/files/{filename}/waveform')
def file_waveform(filename: str, start: float = Query(0, ge=0, le=86400),
                  end: Optional[float] = Query(None, gt=0, le=86400), points: int = Query(1000, ge=50, le=4000)):
    return library_waveform(filename, start=start, end=end, points=points)


@router.post('/api/files/{filename}/clip')
async def clip_file(filename: str, req: ClipRequest):
    return await clip_library_file(filename, req.start, req.end)


@router.delete('/api/files/{filename}')
def delete_file(filename: str):
    p = library_file(filename)
    p.unlink()
    return {'ok': True, 'deleted': filename}
