"""Telemetry validation, missing-value handling, and feature engineering."""

from __future__ import annotations

import math
from typing import Iterable

import pandas as pd

REQUIRED_COLUMNS = [
    "timestamp",
    "ambient_temp_c",
    "humidity_pct",
    "solar_irradiance_wm2",
]


def parse_telemetry(rows: Iterable[dict] | pd.DataFrame) -> pd.DataFrame:
    """Normalize uploaded or posted telemetry into a clean frame.

    Raises ValueError when required columns are missing or no usable rows remain.
    """
    df = pd.DataFrame(rows)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    df = df[REQUIRED_COLUMNS].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for col in REQUIRED_COLUMNS[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
    # Interpolate short weather gaps, then fill remaining edges with column medians.
    weather = REQUIRED_COLUMNS[1:]
    df[weather] = df[weather].interpolate(limit_direction="both")
    df[weather] = df[weather].fillna(df[weather].median(numeric_only=True))
    df = df.dropna(subset=["timestamp"])
    if df.empty:
        raise ValueError("No valid telemetry rows after cleanup.")

    df["ambient_temp_c"] = df["ambient_temp_c"].clip(-20, 55)
    df["humidity_pct"] = df["humidity_pct"].clip(0, 100)
    df["solar_irradiance_wm2"] = df["solar_irradiance_wm2"].clip(0, 1300)
    df["timestamp"] = df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return df.reset_index(drop=True)


def timestamp_parts(ts: pd.Timestamp) -> dict[str, float]:
    hour = int(ts.hour)
    rad = 2 * math.pi * hour / 24.0
    return {
        "hour": hour,
        "day_of_week": int(ts.dayofweek),
        "sin_hour": math.sin(rad),
        "cos_hour": math.cos(rad),
        "sin_2hour": math.sin(2 * rad),
        "cos_2hour": math.cos(2 * rad),
        "is_morning_peak": 1.0 if 7 <= hour <= 9 else 0.0,
        "is_evening_peak": 1.0 if 18 <= hour <= 22 else 0.0,
        "is_weekend": 1.0 if ts.dayofweek >= 5 else 0.0,
    }


MODEL_FEATURE_COLUMNS = [
    "ambient_temp_c",
    "humidity_pct",
    "solar_irradiance_wm2",
    "hour",
    "day_of_week",
    "sin_hour",
    "cos_hour",
    "sin_2hour",
    "cos_2hour",
    "is_morning_peak",
    "is_evening_peak",
    "is_weekend",
    "temp_x_humidity",
    "cooling_degree",
    "temp_lag1",
    "temp_lag3",
    "humidity_lag1",
    "humidity_lag3",
    "irr_lag1",
    "irr_lag3",
    "temp_roll3",
    "irr_roll3",
]

COOLING_BASE_C = 24.0


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Cyclic hour encodings, peak/weekend flags, weather lags, rolling stats.

    Never lags the demand target. A single-row frame fills lags/rolls with the
    current weather so inference still works.
    """
    out = df.copy()
    stamps = pd.to_datetime(out["timestamp"])
    extra = stamps.map(lambda ts: timestamp_parts(pd.Timestamp(ts)))
    extra_df = pd.DataFrame(list(extra), index=out.index)
    out = pd.concat([out, extra_df], axis=1)
    out["temp_x_humidity"] = out["ambient_temp_c"] * out["humidity_pct"] / 100.0
    out["cooling_degree"] = (out["ambient_temp_c"] - COOLING_BASE_C).clip(lower=0)
    temp = out["ambient_temp_c"]
    humidity = out["humidity_pct"]
    irr = out["solar_irradiance_wm2"]
    out["temp_lag1"] = temp.shift(1)
    out["temp_lag3"] = temp.shift(3)
    out["humidity_lag1"] = humidity.shift(1)
    out["humidity_lag3"] = humidity.shift(3)
    out["irr_lag1"] = irr.shift(1)
    out["irr_lag3"] = irr.shift(3)
    out["temp_roll3"] = temp.rolling(3, min_periods=1).mean()
    out["irr_roll3"] = irr.rolling(3, min_periods=1).mean()
    out["temp_lag1"] = out["temp_lag1"].fillna(temp)
    out["temp_lag3"] = out["temp_lag3"].fillna(temp)
    out["humidity_lag1"] = out["humidity_lag1"].fillna(humidity)
    out["humidity_lag3"] = out["humidity_lag3"].fillna(humidity)
    out["irr_lag1"] = out["irr_lag1"].fillna(irr)
    out["irr_lag3"] = out["irr_lag3"].fillna(irr)
    return out


def build_model_features(df: pd.DataFrame) -> pd.DataFrame:
    """Shared train/inference matrix. Weather-only lags; no target leakage."""
    engineered = engineer_features(df)
    missing = [c for c in MODEL_FEATURE_COLUMNS if c not in engineered.columns]
    if missing:
        raise ValueError(f"Feature builder missing columns: {', '.join(missing)}")
    return engineered[MODEL_FEATURE_COLUMNS].astype(float)


def feature_vector(ts: pd.Timestamp, temp: float, humidity: float, irr: float) -> list[float]:
    frame = pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
                "ambient_temp_c": float(temp),
                "humidity_pct": float(humidity),
                "solar_irradiance_wm2": float(irr),
            }
        ]
    )
    row = build_model_features(frame).iloc[0]
    return [1.0, *row.tolist()]
