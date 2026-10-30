"""Tests for Wattcast AI Assistant (backend/assistant.py and /api/assistant route)."""

from __future__ import annotations

import pytest
from backend.assistant import answer_question, _extract_hour
from backend.config import Config
from app import create_app


@pytest.fixture()
def mock_forecast_data():
    """Sample 24-hour forecast data with evening peak and an overload hour."""
    hours = []
    for h in range(24):
        # Peak at hour 19 with 205 kW (overload)
        if h == 19:
            demand = 205.0
            mode = "Renewable Storage Mode"
            overload = True
            reason = "demand 205.0 kW exceeds 160 kW; evening peak window 18:00–22:00; CRITICAL overload ≥ 200 kW"
        elif 18 <= h <= 22:
            demand = 170.0
            mode = "Renewable Storage Mode"
            overload = False
            reason = "demand 170.0 kW exceeds 160 kW; evening peak window 18:00–22:00"
        elif h == 14:
            demand = 165.0
            mode = "Renewable Storage Mode"
            overload = False
            reason = "demand 165.0 kW exceeds 160 kW"
        else:
            demand = 110.0
            mode = "Main Grid Mode"
            overload = False
            reason = "demand ≤ 160 kW and outside evening window"

        hours.append(
            {
                "timestamp": f"2026-06-10 {h:02d}:00:00",
                "hour": h,
                "predicted_demand_kw": demand,
                "grid_mode": mode,
                "battery_soc_pct": max(15.0, 72.0 - (h * 1.5)),
                "critical_overload": overload,
                "reason": reason,
                "peak_window": 18 <= h <= 22,
                "charge_kw": 45.0 if mode == "Main Grid Mode" else 0.0,
                "discharge_kw": demand if mode == "Renewable Storage Mode" else 0.0,
            }
        )

    summary = {
        "active_mode": "Main Grid Mode",
        "active_timestamp": "2026-06-10 12:00:00",
        "active_demand_kw": 110.0,
        "peak_demand_kw": 205.0,
        "mean_demand_kw": 128.5,
        "overload_hours": 1,
        "renewable_hours": 6,
        "main_grid_hours": 18,
        "battery_soc_pct": 37.5,
        "critical": True,
        "hour_count": 24,
        "first_hour": "2026-06-10 00:00:00",
    }
    return {"hours": hours, "summary": summary}


# ==========================================
# 1. Rule-Based Answers Unit Tests
# ==========================================

def test_extract_hour_helper():
    assert _extract_hour("why at 6 pm") == 18
    assert _extract_hour("why at 6pm") == 18
    assert _extract_hour("what happens at 8 am") == 8
    assert _extract_hour("hour 14") == 14
    assert _extract_hour("at 18:00") == 18
    assert _extract_hour("at 12 pm") == 12
    assert _extract_hour("at 12 am") == 0
    assert _extract_hour("midnight status") == 0
    assert _extract_hour("noon demand") == 12
    assert _extract_hour("general question without time") is None


def test_rule_summary(mock_forecast_data):
    res = answer_question("Can you give me a summary of the forecast?", mock_forecast_data)
    assert res["source"] == "rule"
    assert "Summary" in res["answer"]
    assert "205.0 kW" in res["answer"]
    assert "Renewable Storage" in res["answer"]
    assert len(res["suggestions"]) > 0


def test_rule_peak_hour(mock_forecast_data):
    res = answer_question("What is the peak hour?", mock_forecast_data)
    assert res["source"] == "rule"
    assert "205.0 kW" in res["answer"]
    assert "19:00" in res["answer"] or "7 PM" in res["answer"]
    assert "critical overload" in res["answer"].lower()
    assert len(res["suggestions"]) > 0


def test_rule_overload_hours_detected(mock_forecast_data):
    res = answer_question("Are there any overload hours?", mock_forecast_data)
    assert res["source"] == "rule"
    assert "1 hour(s)" in res["answer"]
    assert "205.0 kW" in res["answer"]
    assert len(res["suggestions"]) > 0


def test_rule_overload_hours_clear():
    # Data with no overloads
    safe_data = {
        "hours": [
            {
                "timestamp": "2026-06-10 10:00:00",
                "hour": 10,
                "predicted_demand_kw": 120.0,
                "grid_mode": "Main Grid Mode",
                "battery_soc_pct": 70.0,
                "critical_overload": False,
                "reason": "nominal",
            }
        ],
        "summary": {
            "peak_demand_kw": 120.0,
            "overload_hours": 0,
            "battery_soc_pct": 70.0,
        },
    }
    res = answer_question("Check overload status", safe_data)
    assert res["source"] == "rule"
    assert "Clear" in res["answer"]
    assert "No critical overloads" in res["answer"]


def test_rule_why_storage(mock_forecast_data):
    res = answer_question("Why was storage used?", mock_forecast_data)
    assert res["source"] == "rule"
    assert "160 kW" in res["answer"]
    assert "18:00–22:00" in res["answer"]
    assert "Renewable Storage is active for 6 hour(s)" in res["answer"]


def test_rule_specific_hour(mock_forecast_data):
    # Ask about 6 PM (hour 18)
    res_6pm = answer_question("Why at 6 pm?", mock_forecast_data)
    assert res_6pm["source"] == "rule"
    assert "18:00" in res_6pm["answer"] or "6 PM" in res_6pm["answer"]
    assert "Renewable Storage Mode" in res_6pm["answer"]

    # Ask about 7 PM (hour 19, overload)
    res_7pm = answer_question("What is happening at 19:00?", mock_forecast_data)
    assert res_7pm["source"] == "rule"
    assert "205.0 kW" in res_7pm["answer"]
    assert "CRITICAL OVERLOAD" in res_7pm["answer"]

    # Ask about 8 AM (hour 8, main grid)
    res_8am = answer_question("What is the mode at 8 am?", mock_forecast_data)
    assert res_8am["source"] == "rule"
    assert "Main Grid Mode" in res_8am["answer"]


def test_rule_battery(mock_forecast_data):
    res = answer_question("What is the battery status?", mock_forecast_data)
    assert res["source"] == "rule"
    assert "800 kWh" in res["answer"]
    assert "SOC" in res["answer"]
    assert "45 kW" in res["answer"]


def test_rule_recommendations(mock_forecast_data):
    res = answer_question("What recommendations do you have?", mock_forecast_data)
    assert res["source"] == "rule"
    assert "Recommendations" in res["answer"]
    assert "Overload Mitigation" in res["answer"]


def test_rule_fallback(mock_forecast_data):
    res = answer_question("Hello, who are you?", mock_forecast_data)
    assert res["source"] == "rule"
    assert "Wattcast AI" in res["answer"]
    assert len(res["suggestions"]) > 0


def test_no_forecast_data_graceful():
    res_none = answer_question("What is the peak hour?", None)
    assert res_none["source"] == "rule"
    assert "No forecast data" in res_none["answer"]

    res_empty = answer_question("Give me a summary", {"hours": []})
    assert res_empty["source"] == "rule"
    assert "No forecast" in res_empty["answer"]

    res_hour_none = answer_question("why at 6 pm", None)
    assert res_hour_none["source"] == "rule"
    assert "No forecast data" in res_hour_none["answer"]


def test_llm_fallback_on_unreachable_endpoint(monkeypatch, mock_forecast_data):
    """When LLM_ENDPOINT is configured but fails/times out, seamlessly fall back to rules."""
    monkeypatch.setenv("LLM_ENDPOINT", "http://127.0.0.1:59999/nonexistent")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "mock-token-xyz")

    res = answer_question("What is the peak hour?", mock_forecast_data)
    assert res["source"] == "rule"
    assert "205.0 kW" in res["answer"]


# ==========================================
# 2. Route Integration Tests (POST /api/assistant)
# ==========================================

def test_assistant_route_requires_login(client):
    """Unauthenticated call to /api/assistant must return 401."""
    res = client.post("/api/assistant", json={"question": "What is the peak hour?"})
    assert res.status_code == 401
    assert res.get_json()["error"] == "Authentication required"


def test_assistant_route_empty_question(auth_client):
    """Empty question must return 400."""
    res = auth_client.post("/api/assistant", json={"question": ""})
    assert res.status_code == 400
    assert "Question is required" in res.get_json()["error"]


def test_assistant_route_whitespace_question(auth_client):
    """Whitespace-only question must return 400."""
    res = auth_client.post("/api/assistant", json={"question": "    "})
    assert res.status_code == 400
    assert "Question is required" in res.get_json()["error"]


def test_assistant_route_missing_question_field(auth_client):
    """Missing question key must return 400."""
    res = auth_client.post("/api/assistant", json={})
    assert res.status_code == 400
    assert "Question is required" in res.get_json()["error"]


def test_assistant_route_question_too_long(auth_client):
    """Question exceeding 500 characters must return 400."""
    long_q = "a" * 501
    res = auth_client.post("/api/assistant", json={"question": long_q})
    assert res.status_code == 400
    assert "500 characters" in res.get_json()["error"]


def test_assistant_route_valid_without_forecast(auth_client):
    """Valid question when no forecast has run yet returns 200 with polite prompt."""
    res = auth_client.post("/api/assistant", json={"question": "What is the peak hour?"})
    assert res.status_code == 200
    body = res.get_json()
    assert "answer" in body
    assert body["source"] == "rule"
    assert isinstance(body["suggestions"], list)


def test_assistant_route_valid_with_forecast(auth_client):
    """Valid question after running sample forecast returns accurate answers."""
    # First, run the sample forecast
    sample_res = auth_client.post("/api/forecast/sample")
    assert sample_res.status_code == 200

    # Now ask the assistant
    res = auth_client.post("/api/assistant", json={"question": "What is the peak hour?"})
    assert res.status_code == 200
    body = res.get_json()
    assert "answer" in body
    assert body["source"] == "rule"
    assert len(body["suggestions"]) > 0

    # Ask why storage was used
    storage_res = auth_client.post("/api/assistant", json={"question": "Why was storage used?"})
    assert storage_res.status_code == 200
    storage_body = storage_res.get_json()
    assert "160 kW" in storage_body["answer"]


def test_assistant_rate_limit(tmp_path, monkeypatch):
    """Test rate limiting on /api/assistant (30 per minute)."""
    db_path = tmp_path / "rate_assistant.db"
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
    rate_client.post(
        "/login",
        data={"email": "engineer@hacksphere.local", "password": "Grid@2026"},
        follow_redirects=True,
    )

    statuses = [
        rate_client.post("/api/assistant", json={"question": "Summary?"}).status_code
        for _ in range(35)
    ]
    assert 429 in statuses

