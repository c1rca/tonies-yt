import os
from pathlib import Path

class Settings:

    tonies_email: str = os.getenv("TONIES_EMAIL", "")
    tonies_password: str = os.getenv("TONIES_PASSWORD", "")
    tonies_character_name: str = os.getenv("TONIES_CHARACTER_NAME", "")
    tonies_login_url: str = os.getenv("TONIES_LOGIN_URL", "https://login.tonies.com")
    tonies_app_url: str = os.getenv("TONIES_APP_URL", "https://my.tonies.com")
    tonies_creative_upload_url: str = os.getenv("TONIES_CREATIVE_UPLOAD_URL", "")

    data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))
    app_port: int = int(os.getenv("APP_PORT", "8080"))
    tonies_storage_state_file: Path = Path(os.getenv("TONIES_STORAGE_STATE_FILE", str(Path(os.getenv("DATA_DIR", "./data")) / "tonies-storage-state.json")))
    log_level: str = os.getenv("LOG_LEVEL", "DEBUG")
    log_file: Path = Path(os.getenv("LOG_FILE", str(Path(os.getenv("DATA_DIR", "./data")) / "logs" / "tonies-auto.log")))

    # Optional selector overrides
    sel_email: str = os.getenv("TONIES_LOGIN_EMAIL_SELECTOR", "input[type='email']")
    sel_password: str = os.getenv("TONIES_LOGIN_PASSWORD_SELECTOR", "input[type='password']")
    sel_submit: str = os.getenv("TONIES_LOGIN_SUBMIT_SELECTOR", "button[type='submit']")
    sel_character_search: str = os.getenv("TONIES_SEARCH_CHARACTER_SELECTOR", "input[placeholder*='Search']")
    sel_upload_button: str = os.getenv("TONIES_UPLOAD_BUTTON_SELECTOR", "button:has-text('Upload')")
    sel_file_input: str = os.getenv("TONIES_FILE_INPUT_SELECTOR", "input[type='file']")

settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / "downloads").mkdir(parents=True, exist_ok=True)
settings.log_file.parent.mkdir(parents=True, exist_ok=True)
