import pytest

from app import ai
from app.models import ChatRequest
from pydantic import ValidationError


def test_chat_request_limits_input_before_job_persistence():
    assert len(ChatRequest(message="x" * 1000).message) == 1000
    with pytest.raises(ValidationError):
        ChatRequest(message="x" * 1001)


@pytest.mark.parametrize("message", ["", "--exec evil", "file:///etc/passwd", "https://example.com/a", "x" * 1001])
def test_parser_rejects_unsafe_sources(message):
    with pytest.raises(ValueError, match="YouTube"):
        ai.parse_request(message)


def test_parser_canonicalizes_youtube_share_link():
    assert ai.parse_request("https://youtu.be/AbCdEf_12-3?si=private").youtube_query == "https://www.youtube.com/watch?v=AbCdEf_12-3"


def test_parse_request_fallback_without_api_key(monkeypatch):
    monkeypatch.setattr(ai.settings, "tonies_character_name", "Blue Tonie")

    result = ai.parse_request("paw patrol bedtime story")

    assert result.action == "download_and_upload"
    assert result.youtube_query == "paw patrol bedtime story"
    assert result.target_character_name == "Blue Tonie"
