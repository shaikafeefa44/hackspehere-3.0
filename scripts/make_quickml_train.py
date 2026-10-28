"""Builds data/train_engineered.csv for QuickML.

Run from the project folder:   python scripts/make_quickml_train.py
Uses the same time features as backend/preprocess.py, so training and
prediction match. The text 'timestamp' column is replaced by numbers.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.preprocess import timestamp_parts  # noqa: E402

SRC = ROOT / "data" / "train_telemetry.csv"
OUT = ROOT / "data" / "train_engineered.csv"

df = pd.read_csv(SRC)
stamps = pd.to_datetime(df["timestamp"])
parts = pd.DataFrame([timestamp_parts(pd.Timestamp(t)) for t in stamps])

keep_parts = ["hour", "day_of_week", "sin_hour", "cos_hour",
              "is_morning_peak", "is_evening_peak", "is_weekend"]
out = pd.concat(
    [df[["ambient_temp_c", "humidity_pct", "solar_irradiance_wm2"]],
     parts[keep_parts],
     df[["target_demand_kw"]]],
    axis=1,
)
out.to_csv(OUT, index=False)
print("Rows:", len(out))
print("Columns:", list(out.columns))
print("Saved:", OUT)