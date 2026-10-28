# HackSphere 3.0 — Smart Energy Microgrid & Demand Forecasting Engine

End-to-end control-room app that ingests weather telemetry, forecasts the next 24 hours of energy demand (`target_demand_kw`), and emits **Main Grid** vs **Renewable Storage** dispatch signals. Built for **Zoho Catalyst** (AppSail + QuickML).

> **Team:** `<TeamName>` · **Live app:** `<public Catalyst AppSail URL>`

---

## Table of contents

- [HackSphere 3.0 — Smart Energy Microgrid \& Demand Forecasting Engine](#hacksphere-30--smart-energy-microgrid--demand-forecasting-engine)
  - [Table of contents](#table-of-contents)
  - [What it does](#what-it-does)
  - [Architecture](#architecture)
  - [Project layout](#project-layout)
  - [Local setup](#local-setup)
  - [QuickML wiring](#quickml-wiring)
  - [Model performance](#model-performance)
  - [Transition controller rules](#transition-controller-rules)
  - [Overload notifications](#overload-notifications)
  - [Catalyst AppSail deploy](#catalyst-appsail-deploy)
  - [API sketch (authenticated)](#api-sketch-authenticated)
  - [Troubleshooting](#troubleshooting)
  - [Evaluation mapping](#evaluation-mapping)
  - [Submission checklist](#submission-checklist)

---

## What it does

1. **Telemetry ingest** — CSV upload (same schema as `data/sample_test_input.csv`) or a live dashboard form.
2. **Demand forecast** — `predict.py` calls a published Catalyst QuickML REST endpoint, or a bundled OLS model if QuickML is not configured.
3. **Transition controller**
   - Demand **> 160 kW** OR hour in **18:00–22:00** (inclusive) → **Renewable Storage Mode**
   - Otherwise → **Main Grid Mode**
   - Demand **≥ 200 kW** → independent **critical overload** alert (email/SMS when credentials are set)
4. **Dashboard** — Chart.js load curve, dispatch strip, battery SOC, alert banner, dark/light mode, CSV/PDF export.
5. **Auth** — engineer signup/login (session cookies, hashed passwords).

## Architecture

```
 Telemetry (CSV / form)
          │
          ▼
  backend/preprocess.py ──► feature engineering
          │
          ▼
      predict.py ──► Catalyst QuickML REST endpoint
          │              (falls back to models/demand_model.json OLS)
          ▼
   24-hour demand forecast
          │
          ▼
    backend/grid.py ──► Main Grid / Renewable Storage dispatch + overload flag
          │
          ├──► SQLite (forecasts, alerts, users)
          ├──► backend alerts ──► email (SMTP) / SMS (Twilio)
          └──► Flask UI + JSON APIs ──► Chart.js dashboard, CSV/PDF export
```

## Project layout

```
app.py                      Flask app (UI + JSON APIs)
predict.py                  Hackathon inference contract
test_eval.py                Pre-submission SUCCESS/FAIL check
backend/                    Preprocess, grid rules, alerts, SQLite, exports
data/train_telemetry.csv    QuickML training set (weather + target_demand_kw)
data/sample_test_input.csv  24-hour inference sample (no target column)
models/demand_model.json    Local OLS fallback coefficients
scripts/                    Telemetry + local model generator
templates/  static/         Dashboard, login, styles
catalyst.json  app-config.json   AppSail deployment descriptors
```

## Local setup

Python 3.10+ recommended (tested on 3.14).

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # or: cp .env.example .env
```

Generate (or regenerate) telemetry and the local model:

```bash
python scripts/generate_telemetry.py
```

Run the contract check, then the app:

```bash
python predict.py --input data/sample_test_input.csv --output predictions.csv
python test_eval.py
python app.py
```

Open [http://127.0.0.1:9000](http://127.0.0.1:9000).

Demo admin: `engineer@hacksphere.local` / `Grid@2026`

## QuickML wiring

1. Upload `data/train_telemetry.csv` to **Catalyst QuickML**.
2. Recommended extra columns (same as `backend/preprocess.py`): `hour`, `day_of_week`, `sin_hour`, `cos_hour`, `is_morning_peak`, `is_evening_peak`, `is_weekend`. If you train on the raw four columns only, set `QUICKML_FEATURE_MODE=raw`.
3. Publish the regression pipeline and copy the REST URL.
4. Put these in `.env` (never commit tokens):

```
QUICKML_ENDPOINT=https://...
QUICKML_AUTH_TOKEN=your-oauth-token
QUICKML_FEATURE_MODE=engineered
```

`predict.py` POSTs JSON of the form `{ "columns": [...], "data": [[...]] }` plus an `input_data` wrapper. If the live endpoint uses a different envelope, adjust `_quickml_payload()` / `_extract_predictions()` in `predict.py`.

If `QUICKML_ENDPOINT` is empty or the call fails, `predict.py` uses the local OLS model so the app keeps working.

## Model performance

Fill in with your own results from QuickML and the local fallback (time-based split, not random).

| Model | MAE (kW) | RMSE (kW) | R² |
| --- | --- | --- | --- |
| Naive baseline (same hour yesterday) | _TBD_ | _TBD_ | _TBD_ |
| Local OLS fallback | _TBD_ | _TBD_ | _TBD_ |
| **QuickML pipeline (published)** | _TBD_ | _TBD_ | _TBD_ |

## Transition controller rules

Implemented in `backend/grid.py`.

| Condition | Dispatch mode | Alert |
| --- | --- | --- |
| Demand ≤ 160 kW and hour outside 18:00–22:00 | Main Grid Mode | none |
| Demand > 160 kW | Renewable Storage Mode | none |
| Hour in 18:00–22:00 (inclusive) | Renewable Storage Mode | none |
| Demand ≥ 200 kW | Renewable Storage Mode | **Critical overload** |

The overload alert is independent of the mode decision: a 200 kW or higher hour is both in Storage Mode and raises the alert.

## Overload notifications

When any forecast hour is ≥ 200 kW the dashboard banner always fires. Optional delivery:

- **Email** — `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO`
- **SMS** — Twilio `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, `ALERT_SMS_TO`

If those variables are empty, the alert is stored but marked undelivered on that channel.

## Catalyst AppSail deploy

1. Install the [Catalyst CLI](https://docs.catalyst.zoho.com/en/cli/help/cli-command-reference/) and log in.
2. From this folder: `catalyst appsail:deploy` (or the current AppSail deploy command from the HackSphere onboarding mail).
3. Set the same environment variables in the AppSail console that you use in `.env`.
4. Confirm `python app.py` listens on `X_ZOHO_CATALYST_LISTEN_PORT` (already handled in `app.py`).

## API sketch (authenticated)

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/ingest/csv` | Upload telemetry CSV and forecast |
| POST | `/api/ingest/form` | JSON `{ "rows": [ {...} ] }` |
| POST | `/api/forecast/sample` | Run bundled 24-hour sample |
| GET | `/api/forecast/latest` | Latest dispatch + summary |
| GET | `/api/export/csv` / `/api/export/pdf` | Reports |
| GET | `/health` | Liveness |

## Troubleshooting

| Problem | Fix |
| --- | --- |
| QuickML returns an unexpected response shape | Adjust `_quickml_payload()` / `_extract_predictions()` in `predict.py` |
| QuickML calls fail with 401 | The OAuth token has expired; generate a new one and update `QUICKML_AUTH_TOKEN` |
| Predictions look wrong after training on raw columns | Set `QUICKML_FEATURE_MODE=raw` so inference matches training |
| App not reachable after deploy | Confirm the app listens on `X_ZOHO_CATALYST_LISTEN_PORT` and the env vars are set in the AppSail console |
| Alerts show as undelivered | SMTP/Twilio variables are empty or incorrect |
| `test_eval.py` fails | Re-run `python scripts/generate_telemetry.py`, then `python predict.py --input data/sample_test_input.csv --output predictions.csv` |

## Evaluation mapping

| Criterion | Where it lives |
| --- | --- |
| Model accuracy (35%) | QuickML + `predict.py` |
| Deployment (35%) | Flask UI on AppSail |
| Transition logic (20%) | `backend/grid.py` |
| UI (10%) | `templates/dashboard.html` + Chart.js |

## Submission checklist

- [ ] Public Catalyst app URL
- [ ] This repository (or `TeamName_Submission.zip`)
- [ ] Working `predict.py` pointed at your published QuickML endpoint
- [ ] `python test_eval.py` prints SUCCESS
- [ ] No secrets committed (`.env` is git-ignored)
- [ ] This README
