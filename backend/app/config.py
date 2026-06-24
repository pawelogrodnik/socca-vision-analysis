from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
STORAGE_DIR = Path(os.getenv("ORLIK_STORAGE_DIR", ROOT_DIR / "storage")).resolve()
MATCHES_DIR = STORAGE_DIR / "matches"
DATABASE_DIR = STORAGE_DIR / "database"
DATABASE_PATH = Path(os.getenv("ORLIK_DATABASE_PATH", DATABASE_DIR / "orlik.sqlite3")).resolve()
MATCHES_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

DEFAULT_PITCH_WIDTH_M = float(os.getenv("ORLIK_DEFAULT_PITCH_WIDTH_M", "30"))
DEFAULT_PITCH_LENGTH_M = float(os.getenv("ORLIK_DEFAULT_PITCH_LENGTH_M", "47.4"))

APP_MODE = os.getenv("ORLIK_APP_MODE", "local-analysis")  # local-analysis | production-viewer
PUBLISH_TARGET = os.getenv("ORLIK_PUBLISH_TARGET", "local-db")  # local-db | remote-api
PRODUCTION_API_URL = os.getenv("ORLIK_PRODUCTION_API_URL", "").rstrip("/")
PRODUCTION_API_TOKEN = os.getenv("ORLIK_PRODUCTION_API_TOKEN", "")
ADMIN_IMPORT_TOKEN = os.getenv("ORLIK_ADMIN_IMPORT_TOKEN", "")

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ORLIK_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
