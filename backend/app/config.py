"""Runtime configuration loaded from environment (.env).

Security-relevant settings (PUBLIC, API_TOKEN, CORS, ALLOWED_HOSTS, DATA_DIR)
are env-only. Provider settings are read here and surfaced as capability status
via /ready; secrets never leave the backend.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _csv(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str
    port: int
    frontend_port: int
    public: bool
    api_token: str
    cors_origins: list[str]
    allowed_hosts: list[str]
    max_upload_mb: int
    job_timeout: int
    max_pending: int
    retrieval_budget_seconds: int
    retrieval_max_concurrency: int
    mode_default: str

    # provider selectors (values, never printed with secrets)
    websearch_provider: str
    image_provenance_provider: str
    image_detector_provider: str
    text_detector_provider: str
    dense_enabled: bool

    # secrets held server-side only
    _secrets: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "aurora_evidence.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    def secret(self, key: str) -> str:
        return self._secrets.get(key, "")

    def has_secret(self, key: str) -> bool:
        return bool(self._secrets.get(key))

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.media_dir, self.cache_dir, self.derived_dir, self.artifacts_dir):
            d.mkdir(parents=True, exist_ok=True)

    def validate_public(self) -> None:
        """Fail fast if public mode is misconfigured (contract hardening)."""
        if not self.public:
            return
        if len(self.api_token) < 32:
            raise RuntimeError("AURORA_PUBLIC=true requires AURORA_API_TOKEN of >= 32 chars")
        if any(o.strip() == "*" for o in self.cors_origins):
            raise RuntimeError("Wildcard CORS origin is rejected in public mode")
        if not self.allowed_hosts:
            raise RuntimeError("AURORA_ALLOWED_HOSTS must be set in public mode")


@lru_cache
def get_settings() -> Settings:
    data_dir = Path(os.getenv("AURORA_DATA_DIR", "./var")).resolve()
    secrets = {
        "tavily": os.getenv("AURORA_TAVILY_API_KEY", ""),
        "serper": os.getenv("AURORA_SERPER_API_KEY", ""),
        "mafindo": os.getenv("AURORA_MAFINDO_API_KEY", ""),
        "serpapi": os.getenv("AURORA_SERPAPI_API_KEY", ""),
        "sightengine_user": os.getenv("AURORA_SIGHTENGINE_API_USER", ""),
        "sightengine_secret": os.getenv("AURORA_SIGHTENGINE_API_SECRET", ""),
        "hive": os.getenv("AURORA_HIVE_API_KEY", ""),
        "gptzero": os.getenv("AURORA_GPTZERO_API_KEY", ""),
        "copyleaks": os.getenv("AURORA_COPYLEAKS_API_KEY", ""),
        "copyleaks_email": os.getenv("AURORA_COPYLEAKS_EMAIL", ""),
    }
    return Settings(
        data_dir=data_dir,
        host=os.getenv("AURORA_HOST", "127.0.0.1"),
        port=_int("AURORA_PORT", 8102),
        frontend_port=_int("AURORA_FRONTEND_PORT", 5172),
        public=_bool("AURORA_PUBLIC", False),
        api_token=os.getenv("AURORA_API_TOKEN", ""),
        cors_origins=_csv("AURORA_CORS", "http://localhost:5172,http://127.0.0.1:5172"),
        allowed_hosts=_csv("AURORA_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver"),
        max_upload_mb=_int("AURORA_MAX_UPLOAD_MB", 10),
        job_timeout=_int("AURORA_JOB_TIMEOUT", 180),
        max_pending=_int("AURORA_MAX_PENDING", 20),
        retrieval_budget_seconds=_int("AURORA_RETRIEVAL_BUDGET_SECONDS", 45),
        retrieval_max_concurrency=_int("AURORA_RETRIEVAL_MAX_CONCURRENCY", 6),
        mode_default=os.getenv("AURORA_MODE_DEFAULT", "demo"),
        websearch_provider=os.getenv("AURORA_WEBSEARCH_PROVIDER", "none"),
        image_provenance_provider=os.getenv("AURORA_IMAGE_PROVENANCE_PROVIDER", "none"),
        image_detector_provider=os.getenv("AURORA_IMAGE_DETECTOR_PROVIDER", "none"),
        text_detector_provider=os.getenv("AURORA_TEXT_DETECTOR_PROVIDER", "none"),
        dense_enabled=_bool("AURORA_DENSE_ENABLED", False),
        _secrets=secrets,
    )
