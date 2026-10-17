from pydantic import BaseModel, Field
from typing import Optional, Literal

from .youtube_source import MAX_INPUT_LENGTH


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_INPUT_LENGTH)
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
    target_position: Optional[dict] = None
    tonies_action_id: Optional[str] = None
    upload_positioned: bool = False
    uploaded_chapter_id: Optional[str] = None
    tonies_content: Optional[dict] = None
    # Accepted-but-unconfirmed attachment: historical evidence, never current inventory.
    attachment_receipt: Optional[dict] = None
    error: Optional[str] = None
    cancel_requested: bool = False
    needs_refresh: bool = False
