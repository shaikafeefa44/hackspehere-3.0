"""SQLite persistence for users, forecasts, dispatch logs, and alerts."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from backend.config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'engineer',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forecast_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    source TEXT NOT NULL,
    model_source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS dispatch_hours (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    predicted_demand_kw REAL NOT NULL,
    grid_mode TEXT NOT NULL,
    battery_soc_pct REAL NOT NULL,
    critical_overload INTEGER NOT NULL,
    reason TEXT NOT NULL,
    hour INTEGER,
    peak_window INTEGER,
    charge_kw REAL,
    discharge_kw REAL,
    FOREIGN KEY(run_id) REFERENCES forecast_runs(id)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    channel TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    Config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_HOUR_EXTRA_COLS = {
    "hour": "INTEGER",
    "peak_window": "INTEGER",
    "charge_kw": "REAL",
    "discharge_kw": "REAL",
}


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(dispatch_hours)")}
        for name, ddl in _HOUR_EXTRA_COLS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE dispatch_hours ADD COLUMN {name} {ddl}")


def hydrate_hour(row: dict) -> dict:
    """Ensure DB-loaded hours expose the same keys as apply_transition_rules()."""
    from backend.config import Config
    from backend.grid import hour_from_timestamp

    hour = row.get("hour")
    if hour is None:
        hour = hour_from_timestamp(row.get("timestamp"))
    hour = int(hour)
    peak = row.get("peak_window")
    if peak is None:
        peak = hour in Config.PEAK_HOURS
    demand = float(row.get("predicted_demand_kw") or 0.0)
    mode = str(row.get("grid_mode") or "")
    charge = row.get("charge_kw")
    discharge = row.get("discharge_kw")
    if charge is None or discharge is None:
        if "Renewable" in mode:
            charge = 0.0
            discharge = demand
        else:
            charge = 45.0
            discharge = 0.0
    overload = row.get("critical_overload")
    return {
        **row,
        "hour": hour,
        "peak_window": bool(peak),
        "charge_kw": float(charge),
        "discharge_kw": float(discharge),
        "critical_overload": bool(overload),
        "predicted_demand_kw": demand,
        "battery_soc_pct": float(row.get("battery_soc_pct") or 0.0),
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def save_forecast(user_id: int | None, source: str, model_source: str, summary: dict, hours: list[dict]) -> int:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO forecast_runs (user_id, source, model_source, created_at, summary_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, source, model_source, now_iso(), json.dumps(summary)),
        )
        run_id = int(cur.lastrowid)
        conn.executemany(
            """
            INSERT INTO dispatch_hours
                (run_id, timestamp, predicted_demand_kw, grid_mode, battery_soc_pct,
                 critical_overload, reason, hour, peak_window, charge_kw, discharge_kw)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    h["timestamp"],
                    h["predicted_demand_kw"],
                    h["grid_mode"],
                    h["battery_soc_pct"],
                    1 if h["critical_overload"] else 0,
                    h["reason"],
                    int(h.get("hour") if h.get("hour") is not None else 0),
                    1 if h.get("peak_window") else 0,
                    float(h.get("charge_kw") or 0.0),
                    float(h.get("discharge_kw") or 0.0),
                )
                for h in hours
            ],
        )
        return run_id


def latest_run(user_id: int | None = None) -> dict | None:
    with connect() as conn:
        if user_id is None:
            run = conn.execute("SELECT * FROM forecast_runs ORDER BY id DESC LIMIT 1").fetchone()
        else:
            run = conn.execute(
                "SELECT * FROM forecast_runs WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        if not run:
            return None
        hours = conn.execute(
            "SELECT * FROM dispatch_hours WHERE run_id = ? ORDER BY timestamp",
            (run["id"],),
        ).fetchall()
        alerts = conn.execute(
            "SELECT * FROM alerts WHERE run_id = ? ORDER BY id",
            (run["id"],),
        ).fetchall()
        return {
            "run": dict(run),
            "hours": [hydrate_hour(dict(h)) for h in hours],
            "alerts": [dict(a) for a in alerts],
            "summary": json.loads(run["summary_json"]),
        }


def all_hours(limit: int = 500) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM dispatch_hours ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def insert_alert(run_id: int | None, level: str, message: str, channel: str, delivered: bool) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO alerts (run_id, level, message, channel, delivered, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, level, message, channel, 1 if delivered else 0, now_iso()),
        )


def recent_alerts(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def user_by_email(email: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
    return dict(row) if row else None


def user_by_id(user_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def create_user(name: str, email: str, password_hash: str, role: str = "engineer") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (name, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (name.strip(), email.lower().strip(), password_hash, role, now_iso()),
        )
        return int(cur.lastrowid)


def ensure_demo_user() -> None:
    from werkzeug.security import generate_password_hash

    if user_by_email("engineer@hacksphere.local"):
        return
    create_user(
        "Grid Engineer",
        "engineer@hacksphere.local",
        generate_password_hash("Grid@2026"),
        role="admin",
    )


def count_users() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"])
