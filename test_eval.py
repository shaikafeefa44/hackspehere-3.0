"""Mandatory pre-submission contract check for predict.py.

Run from the project root:
    python test_eval.py

Prints SUCCESS when the inference contract is valid.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "data" / "sample_test_input.csv"
PREDICT = ROOT / "predict.py"


def main() -> None:
    if not INPUT.exists():
        print("FAIL: data/sample_test_input.csv is missing")
        sys.exit(1)
    if not PREDICT.exists():
        print("FAIL: predict.py is missing")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "predictions.csv"
        proc = subprocess.run(
            [sys.executable, str(PREDICT), "--input", str(INPUT), "--output", str(output)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print("FAIL: predict.py exited with a non-zero status")
            print(proc.stdout)
            print(proc.stderr)
            sys.exit(1)
        if not output.exists():
            print("FAIL: predictions.csv was not written")
            sys.exit(1)

        original = pd.read_csv(INPUT)
        pred = pd.read_csv(output)

    expected_cols = ["timestamp", "target_demand_kw"]
    if list(pred.columns) != expected_cols:
        print(f"FAIL: expected columns {expected_cols}, got {list(pred.columns)}")
        sys.exit(1)
    if len(pred) != len(original):
        print(f"FAIL: expected {len(original)} rows, got {len(pred)}")
        sys.exit(1)

    orig_ts = pd.to_datetime(original["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    pred_ts = pd.to_datetime(pred["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    if list(orig_ts) != list(pred_ts):
        print("FAIL: timestamps must be preserved in original order")
        sys.exit(1)
    if pred["target_demand_kw"].isna().any():
        print("FAIL: predictions contain nulls")
        sys.exit(1)
    try:
        numeric = pred["target_demand_kw"].astype(float)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: target_demand_kw is not numeric ({exc})")
        sys.exit(1)
    if (numeric <= 0).any():
        print("FAIL: predicted demand must be positive")
        sys.exit(1)

    print("SUCCESS")


if __name__ == "__main__":
    main()
