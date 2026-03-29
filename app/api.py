
# Compatibility shim: deterministic parser used by pipeline/jobs.
# Some modules import parse_request from app.api.
def parse_request(message: str):
    text = (message or '').strip()
    return {
        'action': 'download_and_upload',
        'youtube_query': text,
        'target_character_name': None,
        'preferred_title': None,
    }
