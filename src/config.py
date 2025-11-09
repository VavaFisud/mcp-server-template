from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables or a .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ecole_directe_username: str = Field(
        alias="ECOLE_DIRECTE_USERNAME",
        description="Identifiant utilisé pour se connecter à EcoleDirecte.",
    )
    ecole_directe_password: str = Field(
        alias="ECOLE_DIRECTE_PASSWORD",
        description="Mot de passe de connexion EcoleDirecte.",
    )
    poke_api_key: str = Field(
        alias="POKE_API_KEY",
        description="Jeton Bearer à utiliser pour l'API inbound SMS de Poke.",
    )
    ecole_directe_user_agent: str = Field(
        default=DEFAULT_USER_AGENT,
        alias="ECOLE_DIRECTE_USER_AGENT",
        description="User-Agent envoyé aux endpoints EcoleDirecte (doit rester stable pour un token donné).",
    )
    poll_interval_seconds: int = Field(
        default=300,
        alias="POLL_INTERVAL_SECONDS",
        ge=60,
        le=3600,
        description="Fréquence de rafraîchissement (en secondes) pour les nouvelles données.",
    )
    schedule_window_days: int = Field(
        default=7,
        alias="SCHEDULE_WINDOW_DAYS",
        ge=1,
        le=30,
        description="Nombre de jours d'emploi du temps à récupérer à chaque synchronisation.",
    )
    http_timeout_seconds: float = Field(
        default=20.0,
        alias="HTTP_TIMEOUT_SECONDS",
        ge=5.0,
        le=60.0,
        description="Timeout applicatif pour les requêtes HTTP.",
    )
    state_file: Path = Field(
        default=Path("data/state.json"),
        alias="STATE_FILE",
        description="Chemin du fichier de persistance (token, cn/cv, snapshots).",
    )
    timeline_window_days: int = Field(
        default=30,
        alias="TIMELINE_WINDOW_DAYS",
        ge=5,
        le=90,
        description="Fenêtre de timeline utilisée pour détecter les nouveaux évènements.",
    )
    poke_webhook_url: Optional[HttpUrl] = Field(
        default=None,
        alias="POKE_WEBHOOK_URL",
        description="URL personnalisée pour envoyer les notifications (par défaut API publique).",
    )
    account_index: int = Field(
        default=0,
        alias="ECOLE_DIRECTE_ACCOUNT_INDEX",
        ge=0,
        description="Index du compte élève à utiliser lorsqu'il y a plusieurs comptes.",
    )

    @field_validator("state_file")
    def ensure_state_dir(cls, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


def load_settings() -> Settings:
    """Helper that instantiates settings."""

    return Settings()
