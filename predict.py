

"""HackSphere 3.0 inference contract.

Usage:
    python predict.py --input sample_test_input.csv --output predictions.csv

The script reads weather telemetry, calls a published Zoho Catalyst QuickML
REST endpoint when configured, and always writes:
    timestamp,target_demand_kw

one row per input row, timestamps preserved. If QuickML is unreachable or
unconfigured, a fitted local OLS model is used so the dashboard and
test_eval.py still run end-to-end.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

log = logging.getLogger("hacksphere.predict")
if not log.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s  %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    log.addHandler(_h)
    log.setLevel(logging.DEBUG)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from backend.local_model import predict_local  # noqa: E402
from backend.preprocess import (  # noqa: E402
    build_model_features,
    engineer_features,
    parse_telemetry,
)

QUICKML_ENDPOINT = os.getenv("QUICKML_ENDPOINT", "").strip()
QUICKML_AUTH_TOKEN = os.getenv("QUICKML_AUTH_TOKEN", "").strip()
QUICKML_FEATURE_MODE = (
    os.getenv("QUICKML_FEATURE_MODE", "engineered").strip().lower()
)
QUICKML_PAYLOAD_STYLE = (
    os.getenv("QUICKML_PAYLOAD_STYLE", "single_row").strip().lower()
)
QUICKML_RETRIES = int(os.getenv("QUICKML_RETRIES", "3"))
QUICKML_ENDPOINT_KEY = os.getenv("QUICKML_ENDPOINT_KEY", "").strip()
CATALYST_ORG = os.getenv("CATALYST_ORG", "").strip()
QUICKML_ENVIRONMENT = os.getenv("QUICKML_ENVIRONMENT", "").strip()
ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "").strip()
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "").strip()
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN", "").strip()
ZOHO_ACCOUNTS_URL = (
    os.getenv(
        "ZOHO_ACCOUNTS_URL",
        "https://accounts.zoho.com",
    )
    .strip()
    .rstrip("/")
)

QUICKML_COLUMNS = [
    "ambient_temp_c",
    "humidity_pct",
    "solar_irradiance_wm2",
    "hour",
    "day_of_week",
    "sin_hour",
    "cos_hour",
    "is_morning_peak",
    "is_evening_peak",
    "is_weekend",
]

_INT_COLUMNS = {
    "hour",
    "day_of_week",
    "is_morning_peak",
    "is_evening_peak",
    "is_weekend",
}

_oauth_cache: dict = {"token": "", "expires_at": 0.0}

_status: dict = {
    "backend": "local-ols",
    "quickml_configured": bool(QUICKML_ENDPOINT),
    "payload_style": QUICKML_PAYLOAD_STYLE,
    "latency_ms": None,
    "error": None,
    "checked_at": None,
}


def _refresh_zoho_token() -> str:
    if not (
        ZOHO_CLIENT_ID
        and ZOHO_CLIENT_SECRET
        and ZOHO_REFRESH_TOKEN
    ):
        return QUICKML_AUTH_TOKEN

    now = time.time()

    if (
        _oauth_cache["token"]
        and now < _oauth_cache["expires_at"] - 30
    ):
        return _oauth_cache["token"]

    url = f"{ZOHO_ACCOUNTS_URL}/oauth/v2/token"

    response = requests.post(
        url,
        data={
            "grant_type": "refresh_token",
            "client_id": ZOHO_CLIENT_ID,
            "client_secret": ZOHO_CLIENT_SECRET,
            "refresh_token": ZOHO_REFRESH_TOKEN,
        },
        timeout=20,
    )

    response.raise_for_status()
    body = response.json()
    token = body.get("access_token")

    if not token:
        raise RuntimeError(
            "Zoho token refresh returned no access_token"
        )

    expires = int(body.get("expires_in") or 3600)

    _oauth_cache["token"] = token
    _oauth_cache["expires_at"] = now + expires

    return token


def _auth_header() -> dict[str, str]:
    token = QUICKML_AUTH_TOKEN

    if ZOHO_REFRESH_TOKEN:
        token = _refresh_zoho_token()

    if not token:
        return {}

    if not token.lower().startswith("zoho-") and " " not in token:
        token = f"Zoho-oauthtoken {token}"

    return {"Authorization": token}


def _request_headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        **_auth_header(),
    }

    if QUICKML_ENDPOINT_KEY:
        headers["X-QUICKML-ENDPOINT-KEY"] = QUICKML_ENDPOINT_KEY

    if CATALYST_ORG:
        headers["CATALYST-ORG"] = CATALYST_ORG

    if QUICKML_ENVIRONMENT:
        headers["Environment"] = QUICKML_ENVIRONMENT

    return headers


def _feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    if QUICKML_FEATURE_MODE == "raw":
        return df[
            [
                "timestamp",
                "ambient_temp_c",
                "humidity_pct",
                "solar_irradiance_wm2",
            ]
        ]

    frame = build_model_features(df)[QUICKML_COLUMNS].copy()

    for col in _INT_COLUMNS:
        frame[col] = frame[col].round().astype(int)

    return frame


def _sanitize_json_values(obj):
    """Replace NaN / Inf with None for valid JSON."""
    if isinstance(obj, float) and (
        math.isnan(obj) or math.isinf(obj)
    ):
        return None

    if isinstance(obj, list):
        return [_sanitize_json_values(v) for v in obj]

    if isinstance(obj, dict):
        return {
            k: _sanitize_json_values(v)
            for k, v in obj.items()
        }

    return obj


def _quickml_payload(df: pd.DataFrame) -> dict:
    """Build batch payloads for supported endpoint styles."""
    frame = _feature_frame(df)
    columns = list(frame.columns)

    data = _sanitize_json_values(
        json.loads(frame.to_json(orient="values"))
    )

    rows = [dict(zip(columns, row)) for row in data]
    style = QUICKML_PAYLOAD_STYLE

    log.debug(
        "QuickML payload style=%s  features=%d  rows=%d",
        style,
        len(columns),
        len(data),
    )

    if style == "row_values":
        return {"row_values": rows}

    if style == "input_data":
        return {
            "input_data": {
                "columns": columns,
                "data": data,
            }
        }

    return {"columns": columns, "data": data}


def _single_row_bodies(df: pd.DataFrame) -> list[dict]:
    frame = _feature_frame(df)
    columns = list(frame.columns)

    data = _sanitize_json_values(
        json.loads(frame.to_json(orient="values"))
    )

    return [
        {"data": dict(zip(columns, row))}
        for row in data
    ]


def _extract_predictions(body, _depth: int = 0) -> list[float]:
    """Parse supported QuickML prediction response formats."""
    if _depth > 5:
        raise ValueError("QuickML response nested too deeply")

    if body is None:
        raise ValueError("Empty QuickML response")

    if isinstance(body, list):
        vals: list[float] = []

        for item in body:
            if isinstance(item, (int, float)):
                vals.append(float(item))

            elif isinstance(item, dict):
                for key in (
                    "target_demand_kw",
                    "prediction",
                    "predicted_value",
                    "output",
                    "value",
                    "result",
                ):
                    if key in item:
                        vals.append(float(item[key]))
                        break
                else:
                    numeric = [
                        v
                        for v in item.values()
                        if isinstance(v, (int, float))
                    ]

                    if not numeric:
                        raise ValueError(
                            f"Cannot parse prediction item: {item}"
                        )

                    vals.append(float(numeric[-1]))

            elif isinstance(item, list) and item:
                vals.append(float(item[-1]))

        for i, value in enumerate(vals):
            if not math.isfinite(value):
                raise ValueError(
                    f"Non-finite prediction at index {i}: {value}"
                )

        return vals

    if isinstance(body, dict):
        for key in (
            "regression_result",
            "prediction",
            "predictions",
            "output",
            "data",
            "result",
            "predicted_values",
        ):
            if key in body:
                return _extract_predictions(
                    body[key],
                    _depth + 1,
                )

    raise ValueError(
        f"Unrecognized QuickML response shape: "
        f"{type(body).__name__}"
    )


def _post_once(
    session: requests.Session,
    body: dict,
) -> list[float]:
    headers = _request_headers()

    response = session.post(
        QUICKML_ENDPOINT,
        headers=headers,
        json=body,
        timeout=45,
    )

    if response.status_code in {401, 403} and ZOHO_REFRESH_TOKEN:
        log.warning(
            "QuickML returned %d; refreshing OAuth token and retrying",
            response.status_code,
        )

        _oauth_cache["expires_at"] = 0

        response = session.post(
            QUICKML_ENDPOINT,
            headers=_request_headers(),
            json=body,
            timeout=45,
        )

    if response.status_code >= 400:
        raise requests.HTTPError(
            f"{response.status_code} from QuickML: "
            f"{response.text[:300]}"
        )

    return _extract_predictions(response.json())


def _post_with_retries(
    session: requests.Session,
    body: dict,
    attempts: int,
) -> list[float]:
    last_exc: Exception | None = None

    for attempt in range(attempts):
        try:
            return _post_once(session, body)

        except Exception as exc:  # noqa: BLE001
            last_exc = exc

            log.warning(
                "QuickML attempt %d/%d failed: %s",
                attempt + 1,
                attempts,
                exc,
            )

            time.sleep(min(2**attempt, 4))

    raise last_exc or RuntimeError("QuickML failed")


def call_quickml(df: pd.DataFrame) -> list[float]:
    if not QUICKML_ENDPOINT:
        raise RuntimeError("QUICKML_ENDPOINT is not set")

    attempts = max(1, QUICKML_RETRIES)
    started = time.perf_counter()

    # Corrected logging: supply all three format arguments.
    log.info(
        "QuickML request  endpoint=%s  rows=%d  style=%s",
        QUICKML_ENDPOINT,
        len(df),
        QUICKML_PAYLOAD_STYLE,
    )

    if QUICKML_PAYLOAD_STYLE == "single_row":
        bodies = _single_row_bodies(df)

        # Send one request at a time instead of four concurrently.
        with requests.Session() as session:
            results = [
                _post_with_retries(session, body, attempts)
                for body in bodies
            ]

        preds: list[float] = []

        for result in results:
            if len(result) != 1:
                raise ValueError(
                    f"QuickML returned {len(result)} values for one row"
                )

            preds.append(result[0])

    else:
        with requests.Session() as session:
            preds = _post_with_retries(
                session,
                _quickml_payload(df),
                attempts,
            )

    if len(preds) != len(df):
        raise ValueError(
            f"QuickML returned {len(preds)} predictions "
            f"for {len(df)} rows"
        )

    elapsed = round(
        (time.perf_counter() - started) * 1000,
        1,
    )

    _status["latency_ms"] = elapsed
    _status["error"] = None
    _status["backend"] = "quickml"
    _status["checked_at"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(),
    )

    log.info(
        "QuickML SUCCESS  rows=%d  latency=%.1fms  backend=quickml",
        len(preds),
        elapsed,
    )

    return [float(value) for value in preds]


def predict_frame(
    df: pd.DataFrame,
    require_quickml: bool = False,
) -> pd.DataFrame:
    clean = parse_telemetry(df)

    log.info(
        "predict_frame  input_rows=%d  quickml_configured=%s",
        len(clean),
        bool(QUICKML_ENDPOINT),
    )

    source = "local-ols"

    try:
        values = call_quickml(clean)
        source = "quickml"

    except Exception as exc:  # noqa: BLE001
        _status["error"] = str(exc)
        _status["backend"] = "local-ols"
        _status["checked_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
        )

        log.warning(
            "QuickML unavailable (%s); falling back to local OLS model.",
            exc,
        )

        if require_quickml:
            raise

        started = time.perf_counter()
        values = predict_local(clean)

        elapsed = round(
            (time.perf_counter() - started) * 1000,
            1,
        )

        _status["latency_ms"] = elapsed

        log.info(
            "Local-OLS SUCCESS  rows=%d  latency=%.1fms  backend=local-ols",
            len(values),
            elapsed,
        )

    out = pd.DataFrame(
        {
            "timestamp": clean["timestamp"],
            "target_demand_kw": [
                round(value, 2) for value in values
            ],
        }
    )

    out.attrs["source"] = source
    out.attrs["quality"] = getattr(
        clean,
        "attrs",
        {},
    ).get("quality")

    _status["quickml_configured"] = bool(QUICKML_ENDPOINT)
    _status["payload_style"] = QUICKML_PAYLOAD_STYLE

    log.info(
        "predict_frame complete  source=%s  rows=%d",
        source,
        len(out),
    )

    return out


def model_status(probe: bool = False) -> dict:
    if probe and QUICKML_ENDPOINT:
        sample = ROOT / "data" / "sample_test_input.csv"

        if sample.exists():
            try:
                raw = pd.read_csv(sample)
                predict_frame(
                    raw.head(1),
                    require_quickml=True,
                )

            except Exception as exc:  # noqa: BLE001
                _status["error"] = str(exc)
                _status["backend"] = "local-ols"

    return dict(_status)


def predict_from_csv(
    input_path: str | Path,
    output_path: str | Path,
    require_quickml: bool = False,
) -> pd.DataFrame:
    raw = pd.read_csv(input_path)

    result = predict_frame(
        raw,
        require_quickml=require_quickml,
    )

    Path(output_path).parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    result.to_csv(output_path, index=False)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HackSphere demand forecast inference"
    )

    parser.add_argument(
        "--input",
        required=True,
        help="Path to sample_test_input.csv style file",
    )

    parser.add_argument(
        "--output",
        required=True,
        help="Destination predictions.csv",
    )

    parser.add_argument(
        "--require-quickml",
        action="store_true",
        help="Fail instead of using the local OLS/GB fallback",
    )

    args = parser.parse_args()

    result = predict_from_csv(
        args.input,
        args.output,
        require_quickml=args.require_quickml,
    )

    print(
        f"Wrote {len(result)} predictions to {args.output} "
        f"(source: {result.attrs.get('source')})"
    )


if __name__ == "__main__":
    main()
