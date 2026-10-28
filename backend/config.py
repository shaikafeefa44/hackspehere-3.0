"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

_WEAK_SECRETS = {
    "",
    "change-me-in-production",
    "hacksphere-dev-secret-change-me",
    "secret",
    "dev",
}


def _resolve_secret_key() -> str:
    raw = (os.getenv("SECRET_KEY") or "").strip()
    deployed = bool(os.getenv("X_ZOHO_CATALYST_LISTEN_PORT"))
    production = os.getenv("FLASK_ENV", "").lower() == "production"
    require = os.getenv("REQUIRE_STRONG_SECRET", "0") == "1" or deployed or production
    strong = raw not in _WEAK_SECRETS and len(raw) >= 16
    if strong:
        return raw
    if require:
        raise RuntimeError(
            "SECRET_KEY must be a random string of at least 16 characters. "
            "Set it in .env or the AppSail environment (REQUIRE_STRONG_SECRET / production)."
        )
    generated = secrets.token_hex(32)
    print(
        "[config] SECRET_KEY is missing or weak; using an ephemeral key. "
        "Sessions will reset on restart. Set SECRET_KEY before deploying.",
        file=sys.stderr,
    )
    return generated


class Config:
    SECRET_KEY = _resolve_secret_key()
    DATABASE_PATH = Path(os.getenv("DATABASE_PATH", ROOT / "instance" / "microgrid.db"))
    UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", ROOT / "uploads"))
    EXPORT_DIR = Path(os.getenv("EXPORT_DIR", ROOT / "exports"))
    DATA_DIR = ROOT / "data"
    MODEL_PATH = ROOT / "models" / "demand_model.json"
    GB_MODEL_PATH = ROOT / "models" / "demand_gb.joblib"
    SAMPLE_CSV = DATA_DIR / "sample_test_input.csv"

    STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").strip()
    CATALYST_DATASTORE_TABLE = os.getenv("CATALYST_DATASTORE_TABLE", "MicrogridBlobs")
    STRATUS_BUCKET = os.getenv("STRATUS_BUCKET", "hacksphere-microgrid")

    QUICKML_ENDPOINT = os.getenv("QUICKML_ENDPOINT", "").strip()
    QUICKML_AUTH_TOKEN = os.getenv("QUICKML_AUTH_TOKEN", "").strip()
    QUICKML_FEATURE_MODE = os.getenv("QUICKML_FEATURE_MODE", "engineered").strip()
    QUICKML_PAYLOAD_STYLE = os.getenv("QUICKML_PAYLOAD_STYLE", "columns_data").strip()
    QUICKML_RETRIES = int(os.getenv("QUICKML_RETRIES", "3"))
    ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "").strip()
    ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "").strip()
    ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN", "").strip()
    ZOHO_ACCOUNTS_URL = os.getenv("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com").strip().rstrip("/")

    ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "")
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "hacksphere-alerts@example.com")

    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM = os.getenv("TWILIO_FROM", "")
    ALERT_SMS_TO = os.getenv("ALERT_SMS_TO", "")

    PEAK_DEMAND_KW = 160.0
    OVERLOAD_KW = 200.0
    PEAK_HOURS = frozenset(range(18, 23))  # 18:00 through 22:00 inclusive
    BATTERY_CAPACITY_KWH = 800.0
    INITIAL_SOC_PCT = 72.0
    SOC_MIN_PCT = 10.0
    SOC_MAX_PCT = 95.0
    BATTERY_EFFICIENCY = 0.92
    MAX_CHARGE_KW = 120.0
    MAX_DISCHARGE_KW = 250.0
    SOLAR_KW_PER_WM2 = 0.12  # ~115 kW at 960 W/m²
    GRID_TRICKLE_CHARGE_KW = 45.0
