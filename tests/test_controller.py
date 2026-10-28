from __future__ import annotations

import pandas as pd

from backend.grid import MAIN_GRID, RENEWABLE, apply_transition_rules, classify_hour


def ts(hour: int, day: str = "2026-06-10") -> str:
    return f"{day} {hour:02d}:00:00"


def test_demand_160_is_main_grid_outside_window():
    decision = classify_hour(160.0, ts(12))
    assert decision["grid_mode"] == MAIN_GRID
    assert decision["critical_overload"] is False
    assert decision["peak_window"] is False
    assert decision["hour"] == 12


def test_demand_160_01_is_renewable_storage():
    decision = classify_hour(160.01, ts(12))
    assert decision["grid_mode"] == RENEWABLE
    assert decision["critical_overload"] is False


def test_hour_17_outside_peak_is_main_grid():
    decision = classify_hour(100.0, ts(17))
    assert decision["hour"] == 17
    assert decision["peak_window"] is False
    assert decision["grid_mode"] == MAIN_GRID


def test_hour_18_inclusive_is_renewable():
    decision = classify_hour(100.0, ts(18))
    assert decision["peak_window"] is True
    assert decision["grid_mode"] == RENEWABLE


def test_hour_22_inclusive_is_renewable():
    decision = classify_hour(100.0, ts(22))
    assert decision["peak_window"] is True
    assert decision["grid_mode"] == RENEWABLE


def test_hour_23_outside_peak_is_main_grid():
    decision = classify_hour(100.0, ts(23))
    assert decision["peak_window"] is False
    assert decision["grid_mode"] == MAIN_GRID


def test_overload_199_99_is_not_critical():
    decision = classify_hour(199.99, ts(12))
    assert decision["critical_overload"] is False
    assert decision["grid_mode"] == RENEWABLE


def test_overload_200_is_critical():
    decision = classify_hour(200.0, ts(12))
    assert decision["critical_overload"] is True
    assert decision["grid_mode"] == RENEWABLE


def test_overload_inside_evening_window():
    decision = classify_hour(210.0, ts(20))
    assert decision["grid_mode"] == RENEWABLE
    assert decision["peak_window"] is True
    assert decision["critical_overload"] is True
    assert "CRITICAL" in decision["reason"]


def test_transition_records_include_charge_fields():
    frame = pd.DataFrame(
        {
            "timestamp": [ts(12), ts(18)],
            "target_demand_kw": [80.0, 80.0],
        }
    )
    hours = apply_transition_rules(frame)
    assert hours[0]["grid_mode"] == MAIN_GRID
    assert hours[0]["charge_kw"] > 0
    assert hours[0]["discharge_kw"] == 0
    assert hours[1]["grid_mode"] == RENEWABLE
    assert hours[1]["charge_kw"] == 0
    assert hours[1]["discharge_kw"] == 80.0
    for key in ("hour", "peak_window", "charge_kw", "discharge_kw", "battery_soc_pct"):
        assert key in hours[0]
