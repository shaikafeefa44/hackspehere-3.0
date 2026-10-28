"""On-device demand model used when QuickML is not configured.

Prefers a Gradient Boosting joblib artifact when scikit-learn is installed;
otherwise ordinary least squares coefficients in demand_model.json.
"""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from backend.config import Config
from backend.preprocess import MODEL_FEATURE_COLUMNS, build_model_features, feature_vector


@lru_cache(maxsize=1)
def load_model() -> dict:
    if not Config.MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Local model missing at {Config.MODEL_PATH}. Run scripts/generate_telemetry.py "
            "or scripts/train_eval.py"
        )
    return json.loads(Config.MODEL_PATH.read_text(encoding="utf-8"))


def _gb_predict(df: pd.DataFrame) -> list[float] | None:
    path = Config.GB_MODEL_PATH
    if not path.exists():
        return None
    try:
        import joblib  # type: ignore
    except ImportError:
        return None
    try:
        bundle = joblib.load(path)
        model = bundle["model"] if isinstance(bundle, dict) else bundle
        columns = bundle.get("feature_names", MODEL_FEATURE_COLUMNS) if isinstance(bundle, dict) else MODEL_FEATURE_COLUMNS
        feats = build_model_features(df).reindex(columns=columns, fill_value=0.0)
        preds = model.predict(feats.to_numpy(dtype=float))
        return [float(max(20.0, v)) for v in preds]
    except Exception:
        return None


def _design_matrix(df: pd.DataFrame, feature_names: list[str]) -> np.ndarray:
    names = [n for n in feature_names if n != "bias"]
    if set(names) <= set(MODEL_FEATURE_COLUMNS) and len(names) > 8:
        feats = build_model_features(df).reindex(columns=names, fill_value=0.0)
        return np.column_stack([np.ones(len(feats)), feats.to_numpy(dtype=float)])
    rows = []
    for row in df.itertuples(index=False):
        rows.append(
            feature_vector(
                pd.Timestamp(row.timestamp),
                row.ambient_temp_c,
                row.humidity_pct,
                row.solar_irradiance_wm2,
            )
        )
    X = np.array(rows, dtype=float)
    beta_len = len(feature_names)
    if X.shape[1] != beta_len:
        X = X[:, :beta_len] if X.shape[1] > beta_len else np.pad(X, ((0, 0), (0, beta_len - X.shape[1])))
    return X


def predict_local(df: pd.DataFrame) -> list[float]:
    gb = _gb_predict(df)
    if gb is not None:
        return gb
    model = load_model()
    beta = np.array(model["coefficients"], dtype=float)
    names = list(model.get("feature_names") or ["bias", *MODEL_FEATURE_COLUMNS])
    X = _design_matrix(df, names)
    if X.shape[1] != len(beta):
        # Last-resort per-row classic vector if JSON is the original 11-coeff OLS.
        preds = []
        for row in df.itertuples(index=False):
            ts = pd.Timestamp(row.timestamp)
            x = np.array(
                feature_vector(ts, row.ambient_temp_c, row.humidity_pct, row.solar_irradiance_wm2)[: len(beta)],
                dtype=float,
            )
            if len(x) < len(beta):
                x = np.pad(x, (0, len(beta) - len(x)))
            preds.append(float(max(20.0, x @ beta)))
        return preds
    yhat = X @ beta
    return [float(max(20.0, v)) for v in yhat]
