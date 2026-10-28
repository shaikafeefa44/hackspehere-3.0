"""Generate train/test CSVs and fit the local OLS demand model.

Run from the project root:
    python scripts/generate_telemetry.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MODELS = ROOT / "models"

HOURS = 24 * 90  # 90 days of hourly telemetry
START = pd.Timestamp("2024-04-01 00:00:00")


def weather_for(ts: pd.Timestamp, rng: np.random.Generator) -> tuple[float, float, float]:
    hour = ts.hour
    day = ts.dayofyear
    seasonal = 8 * math.sin(2 * math.pi * (day - 80) / 365)
    diurnal = 6 * math.sin(2 * math.pi * (hour - 7) / 24)
    temp = 24.5 + seasonal + diurnal + float(rng.normal(0, 1.1))
    humidity = float(np.clip(62 - 1.4 * diurnal + rng.normal(0, 6), 18, 96))
    # Solar irradiance follows a clipped daytime sinusoid.
    solar_angle = math.cos(2 * math.pi * (hour - 13) / 24)
    cloud = float(np.clip(rng.beta(2.2, 3.4), 0, 1))
    irradiance = max(0.0, 980 * max(solar_angle, 0) * (0.35 + 0.65 * (1 - cloud)))
    if hour < 6 or hour > 19:
        irradiance = 0.0
    return round(temp, 2), round(humidity, 2), round(irradiance, 2)


def true_demand(ts: pd.Timestamp, temp: float, humidity: float, irr: float) -> float:
    hour = ts.hour
    weekend = 1.0 if ts.dayofweek >= 5 else 0.0
    morning = 28.0 if 7 <= hour <= 9 else 0.0
    evening = 62.0 if 18 <= hour <= 22 else 0.0
    shoulder = 14.0 if hour in (10, 11, 16, 17) else 0.0
    night = -18.0 if hour <= 5 else 0.0
    ac = max(0.0, temp - 23.0) * 4.8
    heat = max(0.0, 16.0 - temp) * 2.4
    humidity_term = (humidity - 50.0) * 0.22
    solar_heat = irr * 0.016
    weekend_shift = -8.0 * weekend if 8 <= hour <= 17 else 10.0 * weekend
    demand = (
        108.0
        + morning
        + evening
        + shoulder
        + night
        + ac
        + heat
        + humidity_term
        + solar_heat
        + weekend_shift
    )
    return float(demand)


def feature_row(ts: pd.Timestamp, temp: float, humidity: float, irr: float) -> list[float]:
    hour = ts.hour
    rad = 2 * math.pi * hour / 24
    return [
        1.0,
        temp,
        humidity,
        irr,
        math.sin(rad),
        math.cos(rad),
        math.sin(2 * rad),
        math.cos(2 * rad),
        1.0 if 7 <= hour <= 9 else 0.0,
        1.0 if 18 <= hour <= 22 else 0.0,
        1.0 if ts.dayofweek >= 5 else 0.0,
    ]


def main() -> None:
    DATA.mkdir(exist_ok=True)
    MODELS.mkdir(exist_ok=True)
    rng = np.random.default_rng(42)

    rows = []
    index = pd.date_range(START, periods=HOURS, freq="h")
    for ts in index:
        temp, humidity, irr = weather_for(ts, rng)
        demand = true_demand(ts, temp, humidity, irr) + float(rng.normal(0, 4.5))
        demand = max(42.0, demand)
        rows.append(
            {
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "ambient_temp_c": temp,
                "humidity_pct": humidity,
                "solar_irradiance_wm2": irr,
                "target_demand_kw": round(demand, 2),
            }
        )
    train = pd.DataFrame(rows)
    train.to_csv(DATA / "train_telemetry.csv", index=False)

    # A 24-hour test day that exercises every transition rule, including overload.
    test_start = pd.Timestamp("2024-07-18 00:00:00")
    test_rows = []
    scripted = {
        0: (27.2, 68, 0),
        1: (26.4, 70, 0),
        2: (25.8, 72, 0),
        3: (25.1, 74, 0),
        4: (24.7, 75, 0),
        5: (24.9, 73, 0),
        6: (26.0, 70, 40),
        7: (28.4, 64, 180),
        8: (31.2, 55, 420),
        9: (33.8, 48, 650),
        10: (36.1, 42, 820),
        11: (38.4, 38, 910),
        12: (39.8, 35, 960),
        13: (41.2, 33, 940),  # intended overload
        14: (40.6, 34, 880),
        15: (39.1, 36, 760),
        16: (37.4, 40, 540),
        17: (35.2, 46, 280),
        18: (33.0, 52, 60),
        19: (31.4, 58, 0),
        20: (30.2, 62, 0),
        21: (29.4, 64, 0),
        22: (28.6, 66, 0),
        23: (27.8, 67, 0),
    }
    for hour in range(24):
        ts = test_start + pd.Timedelta(hours=hour)
        temp, humidity, irr = scripted[hour]
        test_rows.append(
            {
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "ambient_temp_c": temp,
                "humidity_pct": humidity,
                "solar_irradiance_wm2": irr,
            }
        )
    pd.DataFrame(test_rows).to_csv(DATA / "sample_test_input.csv", index=False)

    X = np.array(
        [
            feature_row(
                pd.Timestamp(r.timestamp),
                r.ambient_temp_c,
                r.humidity_pct,
                r.solar_irradiance_wm2,
            )
            for r in train.itertuples(index=False)
        ],
        dtype=float,
    )
    y = train["target_demand_kw"].to_numpy(dtype=float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    y_hat = X @ beta
    rmse = float(np.sqrt(np.mean((y - y_hat) ** 2)))
    payload = {
        "feature_names": [
            "bias",
            "ambient_temp_c",
            "humidity_pct",
            "solar_irradiance_wm2",
            "sin_hour",
            "cos_hour",
            "sin_2hour",
            "cos_2hour",
            "is_morning_peak",
            "is_evening_peak",
            "is_weekend",
        ],
        "coefficients": [round(float(c), 8) for c in beta],
        "train_rmse": round(rmse, 4),
        "notes": "Ordinary least squares fallback used when QuickML is not configured.",
    }
    (MODELS / "demand_model.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {len(train)} training rows, 24 test rows, OLS RMSE={rmse:.3f}")


if __name__ == "__main__":
    main()
