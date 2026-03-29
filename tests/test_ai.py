from app import ai


def test_parse_request_fallback_without_api_key(monkeypatch):
    monkeypatch.setattr(ai.settings, "openai_api_key", "")
    monkeypatch.setattr(ai.settings, "tonies_character_name", "Blue Tonie")

    result = ai.parse_request("paw patrol bedtime story")

    assert result.action == "download_and_upload"
    assert result.youtube_query == "paw patrol bedtime story"
    assert result.target_character_name == "Blue Tonie"
