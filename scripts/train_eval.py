"""Time-based train/eval for raw OLS vs engineered OLS vs gradient boosting.

Run from the project root:
    python scripts/train_eval.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.preprocess import MODEL_FEATURE_COLUMNS, build_model_features, parse_telemetry  # noqa: E402

TRAIN_CSV = ROOT / "data" / "train_telemetry.csv"
MODEL_JSON = ROOT / "models" / "demand_model.json"
GB_PATH = ROOT / "models" / "demand_gb.joblib"
METRICS_JSON = ROOT / "models" / "metrics.json"


def _metrics(y_true: np.ndarray, y_hat: np.ndarray) -> dict[str, float]:
    err = y_true - y_hat
    rmse = float(np.sqrt(np.mean(err**2)))
    mae = float(np.mean(np.abs(err)))
    ss_res = float(np.sum(err**2))
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot else 0.0
    return {"rmse": round(rmse, 4), "mae": round(mae, 4), "r2": round(r2, 4)}


def _ols_fit(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def main() -> None:
    raw = pd.read_csv(TRAIN_CSV)
    if "target_demand_kw" not in raw.columns:
        raise SystemExit("data/train_telemetry.csv must include target_demand_kw")
    targets = raw[["timestamp", "target_demand_kw"]].copy()
    targets["timestamp"] = pd.to_datetime(targets["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    weather = parse_telemetry(raw.drop(columns=["target_demand_kw"]))
    weather = weather.merge(targets.drop_duplicates("timestamp"), on="timestamp", how="left")
    weather = weather.dropna(subset=["target_demand_kw"]).sort_values("timestamp").reset_index(drop=True)
    y = weather["target_demand_kw"].to_numpy(dtype=float)

    cut = int(len(weather) * 0.8)
    train, test = weather.iloc[:cut], weather.iloc[cut:]
    y_train, y_test = y[:cut], y[cut:]

    raw_cols = ["ambient_temp_c", "humidity_pct", "solar_irradiance_wm2"]
    X_raw_train = np.column_stack([np.ones(len(train)), train[raw_cols].to_numpy(dtype=float)])
    X_raw_test = np.column_stack([np.ones(len(test)), test[raw_cols].to_numpy(dtype=float)])
    beta_raw = _ols_fit(X_raw_train, y_train)

    feat_train = build_model_features(train)
    feat_test = build_model_features(test)
    X_eng_train = np.column_stack([np.ones(len(feat_train)), feat_train.to_numpy(dtype=float)])
    X_eng_test = np.column_stack([np.ones(len(feat_test)), feat_test.to_numpy(dtype=float)])
    beta_eng = _ols_fit(X_eng_train, y_train)

    results = {
        "raw_ols": _metrics(y_test, X_raw_test @ beta_raw),
        "engineered_ols": _metrics(y_test, X_eng_test @ beta_eng),
    }

    gb_metrics = None
    try:
        from sklearn.ensemble import HistGradientBoostingRegressor

        gb = HistGradientBoostingRegressor(
            max_depth=6,
            learning_rate=0.08,
            max_iter=250,
            random_state=42,
        )
        gb.fit(feat_train.to_numpy(dtype=float), y_train)
        gb_hat = gb.predict(feat_test.to_numpy(dtype=float))
        gb_metrics = _metrics(y_test, gb_hat)
        results["gradient_boosting"] = gb_metrics
        try:
            import joblib

            GB_PATH.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump({"model": gb, "feature_names": MODEL_FEATURE_COLUMNS}, GB_PATH)
        except Exception as exc:  # noqa: BLE001
            print(f"Could not write joblib model: {exc}")
    except ImportError:
        print("scikit-learn not installed; skip gradient boosting (pip install -r requirements-dev.txt)")

    # Refit engineered OLS on all rows for the production fallback.
    feat_all = build_model_features(weather)
    X_all = np.column_stack([np.ones(len(feat_all)), feat_all.to_numpy(dtype=float)])
    beta_prod = _ols_fit(X_all, y)
    payload = {
        "feature_names": ["bias", *MODEL_FEATURE_COLUMNS],
        "coefficients": [round(float(c), 8) for c in beta_prod],
        "train_rmse": results["engineered_ols"]["rmse"],
        "holdout_metrics": results,
        "notes": "Engineered OLS fallback. Prefer QuickML / demand_gb.joblib when available.",
    }
    MODEL_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("Time-based holdout (last 20% of hours)")
    for name, m in results.items():
        print(f"  {name:20s}  RMSE={m['rmse']:.4f}  MAE={m['mae']:.4f}  R2={m['r2']:.4f}")
    print(f"Wrote {MODEL_JSON}")
    if GB_PATH.exists():
        print(f"Wrote {GB_PATH}")


if __name__ == "__main__":
    main()
