import os
import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit


def _allowed_hosts() -> tuple[str, ...]:
    hosts = tuple(host.strip().lower().strip("[]") for host in os.getenv(
        "APP_ALLOWED_HOSTS", "localhost,127.0.0.1,::1").split(",") if host.strip())
    if not hosts:
        raise ValueError("APP_ALLOWED_HOSTS must not be empty")
    for host in hosts:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host):
                raise ValueError("APP_ALLOWED_HOSTS must contain explicit hostnames or IP addresses without ports, wildcards or URLs") from None
    return hosts


def _public_url() -> str:
    value = os.getenv("APP_PUBLIC_URL", "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
            or any(char.isspace() for char in value) or "\\" in value):
        raise ValueError("APP_PUBLIC_URL must be an http(s) origin without a path, credentials, query or fragment")
    _ = parsed.port  # Validate the port (raises ValueError) before accepting the configuration.
    return f"{parsed.scheme}://{parsed.netloc.lower()}"

class Settings:

    tonies_email: str = os.getenv("TONIES_EMAIL", "")
    tonies_password: str = os.getenv("TONIES_PASSWORD", "")
    tonies_character_name: str = os.getenv("TONIES_CHARACTER_NAME", "")
    tonies_creative_upload_url: str = os.getenv("TONIES_CREATIVE_UPLOAD_URL", "")

    data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))
    app_port: int = int(os.getenv("APP_PORT", "8080"))
    allowed_hosts: tuple[str, ...] = _allowed_hosts()
    public_url: str = _public_url()
    max_upload_bytes: int = int(os.getenv("APP_MAX_UPLOAD_BYTES", "104857600"))

    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: Path = Path(os.getenv("LOG_FILE", str(Path(os.getenv("DATA_DIR", "./data")) / "logs" / "tonies-auto.log")))



settings = Settings()
if settings.max_upload_bytes <= 0:
    raise ValueError("APP_MAX_UPLOAD_BYTES must be positive")
settings.data_dir.mkdir(parents=True, exist_ok=True)
(settings.data_dir / "downloads").mkdir(parents=True, exist_ok=True)
settings.log_file.parent.mkdir(parents=True, exist_ok=True)
