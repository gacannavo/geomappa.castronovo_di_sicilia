"""Configurazione letta dalle variabili d'ambiente (vedi .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Carica un file .env semplice (CHIAVE=valore) senza dipendenze esterne."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(Path(os.environ.get("ENV_FILE", ".env")))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    whatsapp_token: str = field(default_factory=lambda: _env("WHATSAPP_TOKEN"))
    whatsapp_app_secret: str = field(default_factory=lambda: _env("WHATSAPP_APP_SECRET"))
    whatsapp_verify_token: str = field(default_factory=lambda: _env("WHATSAPP_VERIFY_TOKEN"))
    whatsapp_phone_number_id: str = field(default_factory=lambda: _env("WHATSAPP_PHONE_NUMBER_ID"))
    graph_api_version: str = field(default_factory=lambda: _env("GRAPH_API_VERSION", "v23.0"))

    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-opus-5-5"))
    claude_effort: str = field(default_factory=lambda: _env("CLAUDE_EFFORT", "medium"))
    business_description: str = field(default_factory=lambda: _env(
        "BUSINESS_DESCRIPTION",
        "una piccola impresa che lavora su appuntamento con clienti privati e aziende"))

    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", "small"))
    whisper_language: str = field(default_factory=lambda: _env("WHISPER_LANGUAGE", "it"))

    dashboard_user: str = field(default_factory=lambda: _env("DASHBOARD_USER", "admin"))
    dashboard_password: str = field(default_factory=lambda: _env("DASHBOARD_PASSWORD"))
    calendar_token: str = field(default_factory=lambda: _env("CALENDAR_TOKEN"))

    owner_whatsapp: str = field(default_factory=lambda: _env("OWNER_WHATSAPP"))
    digest_time: str = field(default_factory=lambda: _env("DIGEST_TIME"))  # es. 19:30
    debounce_seconds: float = field(
        default_factory=lambda: float(_env("ANALYSIS_DEBOUNCE_SECONDS", "90"))
    )
    timezone: str = field(default_factory=lambda: _env("TIMEZONE", "Europe/Rome"))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "assistente.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"


settings = Settings()
