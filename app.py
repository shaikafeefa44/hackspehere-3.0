"""HackSphere 3.0 Smart Energy Microgrid — Flask application.

Serves the dashboard UI and JSON APIs for ingest, forecast, grid state, and exports.
Listens on X_ZOHO_CATALYST_LISTEN_PORT / PORT so the same process runs locally
and on Catalyst AppSail.
"""

from __future__ import annotations

import os
from datetime import timedelta
from functools import wraps
from pathlib import Path

import pandas as pd
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFError, CSRFProtect, generate_csrf
import json
from werkzeug.security import check_password_hash, generate_password_hash
from flask_cors import CORS
from werkzeug.utils import secure_filename

from backend.alerts import handle_overload_alerts
from backend.config import Config
from backend.db import (
    create_user,
    ensure_demo_user,
    init_db,
    latest_run,
    recent_alerts,
    save_forecast,
    user_by_email,
    user_by_id,
    connect,
)
from backend.exports import hours_to_csv, hours_to_pdf
from backend.grid import apply_transition_rules, summarize
from backend.preprocess import parse_telemetry
from backend.storage import get_storage
from predict import predict_frame

ROOT = Path(__file__).resolve().parent
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[], storage_uri="memory://")


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(ROOT / "templates"),
        static_folder=str(ROOT / "static"),
    )
    app.config["SECRET_KEY"] = Config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    app.config["WTF_CSRF_TIME_LIMIT"] = None
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
    app.config["RATELIMIT_ENABLED"] = True
    if test_config:
        app.config.update(test_config)
        db_path = test_config.get("DATABASE_PATH")
        if db_path:
            Config.DATABASE_PATH = Path(db_path)
    Config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    Config.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    csrf.init_app(app)
    limiter.init_app(app)
    CORS(app)
    init_db()
    if app.config.get("ENSURE_DEMO_USER", True):
        ensure_demo_user()
    storage = get_storage()

    def current_user():
        uid = session.get("user_id")
        return user_by_id(uid) if uid else None

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user():
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)

        return wrapped

    @app.context_processor
    def inject_globals():
        return {"current_user": current_user()}

    @app.after_request
    def set_csrf_cookie(response):
        if app.config.get("WTF_CSRF_ENABLED", True):
            response.set_cookie(
                "csrf_token",
                generate_csrf(),
                samesite="Lax",
                httponly=False,
                secure=bool(os.environ.get("X_ZOHO_CATALYST_LISTEN_PORT")),
            )
        return response

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "service": "hacksphere-microgrid"})

    @app.errorhandler(CSRFError)
    def handle_csrf(_err):
        if request.path.startswith("/api/"):
            return jsonify({"error": "CSRF token missing or invalid"}), 400
        flash("Your session expired. Refresh and try again.", "error")
        return redirect(request.url or url_for("login"))

    @app.errorhandler(429)
    def handle_rate_limit(_err):
        if request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
            return jsonify({"error": "Too many login attempts. Wait 5 minutes."}), 429
        flash("Too many login attempts. Please wait 5 minutes.", "error")
        return render_template("login.html"), 429

    @app.get("/")
    def index():
        if current_user():
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    @limiter.limit("5 per 5 minutes", methods=["POST"])
    def login():
        if current_user():
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            user = user_by_email(email)
            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session["user_id"] = user["id"]
                session.permanent = True
                return redirect(request.args.get("next") or url_for("dashboard"))
            flash("Invalid email or password.", "error")
        return render_template("login.html")

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if current_user():
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            if len(name) < 2 or "@" not in email or len(password) < 8:
                flash("Provide a name, valid email, and a password of at least 8 characters.", "error")
                return render_template("signup.html")
            if user_by_email(email):
                flash("An account with that email already exists.", "error")
                return render_template("signup.html")
            uid = create_user(name, email, generate_password_hash(password))
            session.clear()
            session["user_id"] = uid
            return redirect(url_for("dashboard"))
        return render_template("signup.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        return render_template("dashboard.html")

    @app.get("/api/me")
    @login_required
    def api_me():
        user = current_user()
        return jsonify({"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]})

    def _run_forecast(df: pd.DataFrame, source: str):
        predictions = predict_frame(df)
        hours = apply_transition_rules(predictions)
        summary = summarize(hours)
        user = current_user()
        model_source = predictions.attrs.get("source", "unknown")
        run_id = save_forecast(user["id"] if user else None, source, model_source, summary, hours)
        alert_result = handle_overload_alerts(run_id, hours)
        return {
            "run_id": run_id,
            "model_source": model_source,
            "summary": summary,
            "hours": hours,
            "alerts": alert_result,
        }

    @app.post("/api/ingest/csv")
    @login_required
    def ingest_csv():
        upload = request.files.get("file")
        if not upload or not upload.filename:
            return jsonify({"error": "Choose a CSV file to upload."}), 400
        filename = secure_filename(upload.filename)
        if not filename.lower().endswith(".csv"):
            return jsonify({"error": "Only .csv files are accepted."}), 400
        dest = Config.UPLOAD_DIR / filename
        upload.save(dest)
        try:
            storage.save_bytes(f"uploads/{filename}", dest.read_bytes())
            raw = pd.read_csv(dest)
            clean = parse_telemetry(raw)
            result = _run_forecast(clean, source=f"csv:{filename}")
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    @app.post("/api/ingest/form")
    @login_required
    def ingest_form():
        payload = request.get_json(silent=True) or {}
        rows = payload.get("rows")
        if not rows:
            return jsonify({"error": "Submit at least one telemetry row."}), 400
        try:
            clean = parse_telemetry(rows)
            result = _run_forecast(clean, source="live-form")
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    @app.post("/api/forecast")
    @login_required
    def api_forecast():
        """Accept telemetry rows JSON and return forecast result.
        Mirrors the logic of ingest_form but exposed as a dedicated forecast API.
        """
        payload = request.get_json(silent=True) or {}
        rows = payload.get("rows")
        if not rows:
            return jsonify({"error": "Provide telemetry rows under 'rows' key."}), 400
        try:
            clean = parse_telemetry(rows)
            result = _run_forecast(clean, source="api-forecast")
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    @app.post("/api/forecast/sample")
    @login_required
    def forecast_sample():
        if not Config.SAMPLE_CSV.exists():
            return jsonify({"error": "Sample CSV is missing from data/."}), 404
        try:
            raw = pd.read_csv(Config.SAMPLE_CSV)
            clean = parse_telemetry(raw)
            result = _run_forecast(clean, source="sample_test_input.csv")
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    @app.get("/api/forecast/latest")
    @login_required
    def forecast_latest():
        data = latest_run(current_user()["id"])
        if not data:
            return jsonify({"hours": [], "summary": None, "alerts": []})
        return jsonify(
            {
                "run": {
                    "id": data["run"]["id"],
                    "source": data["run"]["source"],
                    "model_source": data["run"]["model_source"],
                    "created_at": data["run"]["created_at"],
                },
                "summary": data["summary"],
                "hours": data["hours"],
                "alerts": data["alerts"],
            }
        )

    @app.get("/api/alerts")
    @login_required
    def api_alerts():
        return jsonify({"alerts": recent_alerts()})

    @app.get("/api/export/csv")
    @login_required
    def export_csv():
        data = latest_run(current_user()["id"])
        if not data or not data["hours"]:
            return jsonify({"error": "Run a forecast before exporting."}), 400
        payload = hours_to_csv(data["hours"])
        path = Config.EXPORT_DIR / "dispatch_log.csv"
        path.write_bytes(payload)
        storage.save_bytes("exports/dispatch_log.csv", payload)
        return send_file(path, as_attachment=True, download_name="microgrid_dispatch.csv")

    # New endpoints for training, prediction, transition, and history
    @app.post("/api/train")
    @login_required
    def api_train():
        """Trigger model training. Uses QuickML if configured, otherwise falls back to local training script.
        Returns status and model source.
        """
        import subprocess
        import sys as _sys
        try:
            script = str(ROOT / "scripts" / "train_eval.py")
            subprocess.run([_sys.executable, script], check=True, cwd=str(ROOT))
            return jsonify({"status": "trained", "model_source": "local"})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.post("/api/predict")
    @login_required
    def api_predict():
        """Accept JSON telemetry rows and return demand predictions.
        Mirrors the logic used by the forecast endpoints.
        """
        payload = request.get_json(silent=True) or {}
        rows = payload.get("rows")
        if not rows:
            return jsonify({"error": "Provide telemetry rows under 'rows' key."}), 400
        try:
            df = pd.DataFrame(rows)
            preds = predict_frame(df)
            result = {
                "model_source": preds.attrs.get("source", "local-ols"),
                "predictions": preds.to_dict(orient="records"),
            }
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/transition")
    @login_required
    def api_transition():
        """Return the latest transition summary and alerts for the current user."""
        data = latest_run(current_user()["id"])
        if not data:
            return jsonify({"error": "No forecast data available."}), 404
        return jsonify({"summary": data["summary"], "alerts": data["alerts"]})

    @app.get("/api/export/pdf")
    @login_required
    def export_pdf():
        data = latest_run(current_user()["id"])
        if not data or not data["hours"]:
            return jsonify({"error": "Run a forecast before exporting."}), 400
        payload = hours_to_pdf(data["hours"], summary=data.get("summary"))
        path = Config.EXPORT_DIR / "dispatch_report.pdf"
        path.write_bytes(payload)
        storage.save_bytes("exports/dispatch_report.pdf", payload)
        return send_file(path, as_attachment=True, download_name="microgrid_dispatch.pdf")

    @app.get("/api/history")
    @login_required
    def api_history():
        """Paginated list of past forecast runs for the user.
        Query params: limit (default 20), offset (default 0).
        """
        limit = int(request.args.get("limit", 20))
        offset = int(request.args.get("offset", 0))
        with connect() as conn:
            rows = conn.execute(
                "SELECT * FROM forecast_runs WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (current_user()["id"], limit, offset),
            ).fetchall()
        runs = []
        for r in rows:
            runs.append({
                "id": r["id"],
                "source": r["source"],
                "model_source": r["model_source"],
                "created_at": r["created_at"],
                "summary": json.loads(r["summary_json"]),
            })
        return jsonify({"runs": runs, "limit": limit, "offset": offset})
    return app



app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("X_ZOHO_CATALYST_LISTEN_PORT") or os.environ.get("PORT") or 9000)
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
