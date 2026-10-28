"""Smart-grid transition controller.

Rules from the HackSphere 3.0 problem statement (evaluated per forecast hour):

* Renewable Storage Mode if predicted demand > 160 kW OR hour is 18:00–22:00 inclusive.
* Main Grid Mode otherwise (demand ≤ 160 kW AND outside the evening window).
* Critical Overload Alert if demand ≥ 200 kW (independent of grid mode).
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from backend.config import Config

RENEWABLE = "Renewable Storage Mode"
MAIN_GRID = "Main Grid Mode"


def hour_from_timestamp(value) -> int:
    ts = pd.to_datetime(value)
    if isinstance(ts, pd.DatetimeIndex):
        ts = ts[0]
    return int(ts.hour)


def classify_hour(demand_kw: float, timestamp) -> dict:
    hour = hour_from_timestamp(timestamp)
    demand = float(demand_kw)
    in_peak_window = hour in Config.PEAK_HOURS
    overload = demand >= Config.OVERLOAD_KW
    if demand > Config.PEAK_DEMAND_KW or in_peak_window:
        mode = RENEWABLE
    else:
        mode = MAIN_GRID
    return {
        "timestamp": _fmt(timestamp),
        "hour": hour,
        "predicted_demand_kw": round(demand, 2),
        "grid_mode": mode,
        "peak_window": in_peak_window,
        "critical_overload": overload,
        "reason": _reason(demand, in_peak_window, overload),
    }


def apply_transition_rules(predictions: pd.DataFrame) -> list[dict]:
    """Return per-hour dispatch decisions plus a simulated battery SOC path."""
    records = []
    soc = Config.INITIAL_SOC_PCT
    capacity = Config.BATTERY_CAPACITY_KWH
    for row in predictions.itertuples(index=False):
        decision = classify_hour(row.target_demand_kw, row.timestamp)
        demand = decision["predicted_demand_kw"]
        if decision["grid_mode"] == RENEWABLE:
            # Discharge storage to cover the predicted load for one hour.
            soc -= (demand / capacity) * 100.0
            charge_kw = 0.0
            discharge_kw = demand
        else:
            # Main-grid hours trickle-charge the battery.
            charge_kw = 45.0
            discharge_kw = 0.0
            soc += (charge_kw / capacity) * 100.0
        soc = max(8.0, min(96.0, soc))
        decision.update(
            {
                "battery_soc_pct": round(soc, 1),
                "charge_kw": charge_kw,
                "discharge_kw": round(discharge_kw, 2),
            }
        )
        records.append(decision)
    return records


def summarize(records: list[dict]) -> dict:
    if not records:
        return {
            "active_mode": MAIN_GRID,
            "peak_demand_kw": 0,
            "mean_demand_kw": 0,
            "overload_hours": 0,
            "renewable_hours": 0,
            "battery_soc_pct": Config.INITIAL_SOC_PCT,
            "critical": False,
        }
    demands = [r["predicted_demand_kw"] for r in records]
    now_like = records[0]
    # Prefer the hour closest to "now" if the forecast covers today; else first hour.
    current = _closest_hour(records)
    overload_hours = sum(1 for r in records if r["critical_overload"])
    return {
        "active_mode": current["grid_mode"],
        "active_timestamp": current["timestamp"],
        "active_demand_kw": current["predicted_demand_kw"],
        "peak_demand_kw": round(max(demands), 2),
        "mean_demand_kw": round(sum(demands) / len(demands), 2),
        "overload_hours": overload_hours,
        "renewable_hours": sum(1 for r in records if r["grid_mode"] == RENEWABLE),
        "main_grid_hours": sum(1 for r in records if r["grid_mode"] == MAIN_GRID),
        "battery_soc_pct": records[-1]["battery_soc_pct"],
        "critical": overload_hours > 0,
        "hour_count": len(records),
        "first_hour": now_like["timestamp"],
    }


def _closest_hour(records: list[dict]) -> dict:
    now_hour = datetime.now().hour
    for rec in records:
        if rec["hour"] == now_hour:
            return rec
    return records[0]


def _fmt(value) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d %H:%M:%S")


def _reason(demand: float, peak_window: bool, overload: bool) -> str:
    bits = []
    if demand > Config.PEAK_DEMAND_KW:
        bits.append(f"demand {demand:.1f} kW exceeds 160 kW")
    if peak_window:
        bits.append("evening peak window 18:00–22:00")
    if not bits:
        bits.append("demand ≤ 160 kW and outside evening window")
    if overload:
        bits.append("CRITICAL overload ≥ 200 kW")
    return "; ".join(bits)
