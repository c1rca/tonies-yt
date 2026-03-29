from pydantic import BaseModel, Field
from typing import Optional, Literal


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    search_limit: Optional[int] = Field(default=5, ge=1, le=20)


class JobResponse(BaseModel):
    job_id: str
    status: str


class ParseIntent(BaseModel):
    action: Literal["download_and_upload"] = "download_and_upload"
    youtube_query: str
    target_character_name: Optional[str] = None
    preferred_title: Optional[str] = None


class JobState(BaseModel):
    id: str
    status: str
    created_at: str
    updated_at: str
    user_message: str
    search_limit: int = 5
    parsed: Optional[dict] = None
    candidates: list[dict] = []
    selected_candidate: Optional[dict] = None
    target_url: Optional[str] = None
    logs: list[str] = []
    output_file: Optional[str] = None
    error: Optional[str] = None
    cancel_requested: bool = False
