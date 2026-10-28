"""Edge-case tests for grid transition rules and predict.py helpers.

Covers the exact boundary values from the HackSphere problem statement:
  - Demand at exactly 160 kW  (Main Grid)  and  160.01 kW  (Renewable)
  - Demand at exactly 199.99 kW (no overload) and 200 kW (critical)
  - Hours 17 (not peak), 18 (peak start), 22 (peak end), 23 (not peak)
  - Combination: overload + peak window
  - Combination: zero demand at midnight
  - Battery SOC clamping across a full 24-hour transition
  - _extract_predictions() with various Catalyst QuickML response shapes
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backend.grid import MAIN_GRID, RENEWABLE, apply_transition_rules, classify_hour, summarize


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def ts(hour: int, day: str = "2026-06-10") -> str:
    return f"{day} {hour:02d}:00:00"


# ---------------------------------------------------------------------------
# 160 kW boundary
# ---------------------------------------------------------------------------
class TestDemand160Boundary:
    def test_exactly_160_outside_peak_is_main_grid(self):
        d = classify_hour(160.0, ts(12))
        assert d["grid_mode"] == MAIN_GRID
        assert d["critical_overload"] is False

    def test_160_epsilon_above_is_renewable(self):
        d = classify_hour(160.0 + 1e-9, ts(12))
        assert d["grid_mode"] == RENEWABLE

    def test_159_99_outside_peak_is_main_grid(self):
        d = classify_hour(159.99, ts(12))
        assert d["grid_mode"] == MAIN_GRID

    def test_160_inside_peak_is_renewable(self):
        """Even though demand <= 160, the peak window forces Renewable."""
        d = classify_hour(160.0, ts(20))
        assert d["grid_mode"] == RENEWABLE
        assert d["peak_window"] is True


# ---------------------------------------------------------------------------
# 200 kW overload boundary
# ---------------------------------------------------------------------------
class TestOverload200Boundary:
    def test_199_99_is_not_critical(self):
        d = classify_hour(199.99, ts(12))
        assert d["critical_overload"] is False
        assert d["grid_mode"] == RENEWABLE  # > 160

    def test_exactly_200_is_critical(self):
        d = classify_hour(200.0, ts(12))
        assert d["critical_overload"] is True
        assert d["grid_mode"] == RENEWABLE

    def test_200_01_is_critical(self):
        d = classify_hour(200.01, ts(12))
        assert d["critical_overload"] is True

    def test_overload_inside_evening_peak(self):
        d = classify_hour(250.0, ts(19))
        assert d["grid_mode"] == RENEWABLE
        assert d["peak_window"] is True
        assert d["critical_overload"] is True
        assert "CRITICAL" in d["reason"]

    def test_overload_outside_evening_peak(self):
        d = classify_hour(210.0, ts(14))
        assert d["grid_mode"] == RENEWABLE
        assert d["peak_window"] is False
        assert d["critical_overload"] is True


# ---------------------------------------------------------------------------
# Peak window hour boundaries: 17 (out), 18 (in), 22 (in), 23 (out)
# ---------------------------------------------------------------------------
class TestPeakHourBoundaries:
    @pytest.mark.parametrize("hour,expected_peak", [
        (0, False), (17, False), (18, True), (19, True),
        (20, True), (21, True), (22, True), (23, False),
    ])
    def test_peak_window_flag(self, hour, expected_peak):
        d = classify_hour(100.0, ts(hour))
        assert d["peak_window"] is expected_peak
        assert d["hour"] == hour

    def test_hour_17_is_main_grid(self):
        d = classify_hour(100.0, ts(17))
        assert d["grid_mode"] == MAIN_GRID

    def test_hour_18_is_renewable(self):
        d = classify_hour(100.0, ts(18))
        assert d["grid_mode"] == RENEWABLE

    def test_hour_22_is_renewable(self):
        d = classify_hour(100.0, ts(22))
        assert d["grid_mode"] == RENEWABLE

    def test_hour_23_is_main_grid(self):
        d = classify_hour(100.0, ts(23))
        assert d["grid_mode"] == MAIN_GRID


# ---------------------------------------------------------------------------
# Combined: low demand at midnight
# ---------------------------------------------------------------------------
def test_zero_demand_midnight():
    d = classify_hour(0.0, ts(0))
    assert d["grid_mode"] == MAIN_GRID
    assert d["peak_window"] is False
    assert d["critical_overload"] is False
    assert d["predicted_demand_kw"] == 0.0


# ---------------------------------------------------------------------------
# apply_transition_rules: battery SOC tracking over 24 hours
# ---------------------------------------------------------------------------
def test_24_hour_soc_stays_within_bounds():
    rows = [{"timestamp": ts(h), "target_demand_kw": 130.0 + h * 5} for h in range(24)]
    frame = pd.DataFrame(rows)
    hours = apply_transition_rules(frame)
    assert len(hours) == 24
    for rec in hours:
        assert 8.0 <= rec["battery_soc_pct"] <= 96.0
        if rec["grid_mode"] == RENEWABLE:
            assert rec["charge_kw"] == 0.0
            assert rec["discharge_kw"] > 0
        else:
            assert rec["charge_kw"] > 0
            assert rec["discharge_kw"] == 0


def test_summarize_marks_critical_when_overload_present():
    rows = [
        {"timestamp": ts(12), "target_demand_kw": 100.0},
        {"timestamp": ts(20), "target_demand_kw": 220.0},
    ]
    frame = pd.DataFrame(rows)
    hours = apply_transition_rules(frame)
    summary = summarize(hours)
    assert summary["critical"] is True
    assert summary["overload_hours"] >= 1
    assert summary["renewable_hours"] >= 1


def test_summarize_not_critical_when_all_normal():
    rows = [{"timestamp": ts(h), "target_demand_kw": 80.0} for h in range(6)]
    frame = pd.DataFrame(rows)
    hours = apply_transition_rules(frame)
    summary = summarize(hours)
    assert summary["critical"] is False
    assert summary["overload_hours"] == 0


# ---------------------------------------------------------------------------
# _extract_predictions: Catalyst QuickML response shapes
# ---------------------------------------------------------------------------
class TestExtractPredictions:
    """Verify the response parser handles all documented Catalyst shapes."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from predict import _extract_predictions
        self.extract = _extract_predictions

    def test_bare_float_list(self):
        assert self.extract([1.0, 2.0, 3.0]) == [1.0, 2.0, 3.0]

    def test_int_list(self):
        assert self.extract([100, 200]) == [100.0, 200.0]

    def test_dict_list_target_demand_kw(self):
        body = [{"target_demand_kw": 150.5}, {"target_demand_kw": 180.2}]
        assert self.extract(body) == [150.5, 180.2]

    def test_dict_list_prediction_key(self):
        body = [{"prediction": 99.0}]
        assert self.extract(body) == [99.0]

    def test_dict_list_predicted_value_key(self):
        body = [{"predicted_value": 42.0}]
        assert self.extract(body) == [42.0]

    def test_predictions_wrapper(self):
        body = {"predictions": [10.0, 20.0]}
        assert self.extract(body) == [10.0, 20.0]

    def test_output_nested_predictions(self):
        body = {"output": {"predictions": [5.0, 6.0]}}
        assert self.extract(body) == [5.0, 6.0]

    def test_regression_result(self):
        body = {"regression_result": [110.0, 120.0]}
        assert self.extract(body) == [110.0, 120.0]

    def test_result_output_nested(self):
        body = {"result": {"output": [7.0, 8.0]}}
        assert self.extract(body) == [7.0, 8.0]

    def test_data_wrapper(self):
        body = {"data": [33.3, 44.4]}
        assert self.extract(body) == [33.3, 44.4]

    def test_nested_list_values(self):
        body = [[0, 100.0], [1, 200.0]]
        assert self.extract(body) == [100.0, 200.0]

    def test_dict_numeric_fallback(self):
        body = [{"col_a": "text", "col_b": 99.5}]
        assert self.extract(body) == [99.5]

    def test_none_body_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            self.extract(None)

    def test_unrecognized_raises(self):
        with pytest.raises(ValueError, match="Unrecognized"):
            self.extract({"unknown_key": "???"})

    def test_non_finite_raises(self):
        with pytest.raises(ValueError, match="Non-finite"):
            self.extract([float("inf")])

    def test_nan_raises(self):
        with pytest.raises(ValueError, match="Non-finite"):
            self.extract([float("nan")])

    def test_deeply_nested_raises(self):
        """Depth > 5 should be rejected."""
        body: dict = {"predictions": {"predictions": {"predictions": {"predictions": {"predictions": {"predictions": [1.0]}}}}}}
        with pytest.raises(ValueError, match="nested too deeply"):
            self.extract(body)


# ---------------------------------------------------------------------------
# reason string content
# ---------------------------------------------------------------------------
def test_reason_mentions_160_when_exceeded():
    d = classify_hour(180.0, ts(12))
    assert "160" in d["reason"]


def test_reason_mentions_evening_in_peak_window():
    d = classify_hour(100.0, ts(20))
    assert "evening" in d["reason"].lower() or "peak" in d["reason"].lower()


def test_reason_mentions_critical_on_overload():
    d = classify_hour(200.0, ts(12))
    assert "CRITICAL" in d["reason"]
