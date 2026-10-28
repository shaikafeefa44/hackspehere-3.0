"""Export the engineered training matrix for Catalyst QuickML.

    python scripts/export_quickml_dataset.py

Writes data/train_quickml.csv (features + target_demand_kw). Does not lag the target.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.preprocess import MODEL_FEATURE_COLUMNS, build_model_features, parse_telemetry  # noqa: E402

SRC = ROOT / "data" / "train_telemetry.csv"
DEST = ROOT / "data" / "train_quickml.csv"


def main() -> None:
    raw = pd.read_csv(SRC)
    targets = raw[["timestamp", "target_demand_kw"]].copy()
    targets["timestamp"] = pd.to_datetime(targets["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    weather = parse_telemetry(raw.drop(columns=["target_demand_kw"]))
    weather = weather.merge(targets.drop_duplicates("timestamp"), on="timestamp", how="left")
    feats = build_model_features(weather)
    out = feats.copy()
    out["target_demand_kw"] = weather["target_demand_kw"].to_numpy()
    out.insert(0, "timestamp", weather["timestamp"].to_numpy())
    DEST.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(DEST, index=False)
    print(f"Wrote {len(out)} rows to {DEST} with columns: {list(out.columns)}")
    unused = [c for c in MODEL_FEATURE_COLUMNS if c not in feats.columns]
    if unused:
        raise SystemExit(f"Missing features: {unused}")


if __name__ == "__main__":
    main()
