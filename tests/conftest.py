from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-32bytes!!")
os.environ.setdefault("REQUIRE_STRONG_SECRET", "0")

from backend.config import Config  # noqa: E402
from app import create_app  # noqa: E402
from backend.db import ensure_demo_user, init_db  # noqa: E402


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "microgrid.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", db_path)
    monkeypatch.setattr(Config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(Config, "EXPORT_DIR", tmp_path / "exports")
    Config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    Config.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    application = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": False,
            "SECRET_KEY": "unit-test-secret-key-32bytes!!",
            "DATABASE_PATH": db_path,
        }
    )
    init_db()
    ensure_demo_user()
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(client):
    client.post(
        "/login",
        data={"email": "engineer@hacksphere.local", "password": "Grid@2026"},
        follow_redirects=True,
    )
    return client


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample_test_input.csv"
