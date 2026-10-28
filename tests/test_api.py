from __future__ import annotations

from backend.db import create_user, hydrate_hour, latest_run, save_forecast
from tests.conftest import SAMPLE


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json()["ok"] is True


def test_api_requires_login(client):
    res = client.get("/api/forecast/latest")
    assert res.status_code == 401


def test_login_and_empty_latest(auth_client):
    res = auth_client.get("/api/forecast/latest")
    assert res.status_code == 200
    body = res.get_json()
    assert body["hours"] == []


def test_sample_forecast_smoke(auth_client):
    res = auth_client.post("/api/forecast/sample")
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["hours"]
    assert SAMPLE.exists()
    first = body["hours"][0]
    for key in ("hour", "peak_window", "charge_kw", "discharge_kw", "grid_mode", "predicted_demand_kw"):
        assert key in first
    evening = [h for h in body["hours"] if int(h["hour"]) in {18, 19, 20, 21, 22}]
    assert evening
    assert all(h["grid_mode"] == "Renewable Storage Mode" for h in evening)


def test_latest_run_is_scoped_to_user(app, auth_client):
    other_id = create_user("Other", "other@hacksphere.local", "pbkdf2:sha256:not-used")
    save_forecast(
        other_id,
        "other",
        "local-ols",
        {"active_mode": "Main Grid Mode"},
        [
            {
                "timestamp": "2026-06-10 12:00:00",
                "hour": 12,
                "predicted_demand_kw": 50.0,
                "grid_mode": "Main Grid Mode",
                "battery_soc_pct": 70.0,
                "critical_overload": False,
                "reason": "quiet",
                "peak_window": False,
                "charge_kw": 45.0,
                "discharge_kw": 0.0,
            }
        ],
    )
    res = auth_client.get("/api/forecast/latest")
    body = res.get_json()
    assert body["hours"] == [] or body.get("run", {}).get("source") != "other"
    scoped = latest_run(user_id=other_id)
    assert scoped is not None
    assert scoped["run"]["source"] == "other"


def test_hydrate_hour_fills_fresh_forecast_fields():
    loaded = hydrate_hour(
        {
            "timestamp": "2026-06-10 20:00:00",
            "predicted_demand_kw": 210.0,
            "grid_mode": "Renewable Storage Mode",
            "battery_soc_pct": 40.0,
            "critical_overload": 1,
            "reason": "peak",
        }
    )
    assert loaded["hour"] == 20
    assert loaded["peak_window"] is True
    assert loaded["discharge_kw"] == 210.0
    assert loaded["charge_kw"] == 0.0
    assert loaded["critical_overload"] is True


def test_login_rate_limit(tmp_path, monkeypatch):
    from backend.config import Config
    from app import create_app

    db_path = tmp_path / "rate.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", db_path)
    application = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "RATELIMIT_ENABLED": True,
            "SECRET_KEY": "unit-test-secret-key-32bytes!!",
            "DATABASE_PATH": db_path,
        }
    )
    rate_client = application.test_client()
    payload = {"email": "engineer@hacksphere.local", "password": "wrong-password"}
    statuses = [rate_client.post("/login", data=payload).status_code for _ in range(6)]
    assert 429 in statuses


def test_csv_ingest(auth_client):
    with SAMPLE.open("rb") as fh:
        res = auth_client.post(
            "/api/ingest/csv",
            data={"file": (fh, "sample_test_input.csv")},
            content_type="multipart/form-data",
        )
    assert res.status_code == 200, res.get_json()
    hours = res.get_json()["hours"]
    assert len(hours) == 24
