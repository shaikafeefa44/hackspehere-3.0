"""Overload alert dispatch: persist banners and optionally email / SMS."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from backend.config import Config
from backend.db import insert_alert


def handle_overload_alerts(run_id: int, hours: list[dict]) -> list[dict]:
    overloads = [h for h in hours if h.get("critical_overload")]
    if not overloads:
        return []

    peak = max(overloads, key=lambda h: h["predicted_demand_kw"])
    message = (
        f"CRITICAL OVERLOAD: {peak['predicted_demand_kw']} kW at {peak['timestamp']} "
        f"({len(overloads)} hour(s) ≥ {Config.OVERLOAD_KW:.0f} kW)."
    )
    emitted = []
    email_ok = _send_email(message)
    insert_alert(run_id, "critical", message, "email", email_ok)
    emitted.append({"channel": "email", "delivered": email_ok, "message": message})

    sms_ok = _send_sms(message)
    insert_alert(run_id, "critical", message, "sms", sms_ok)
    emitted.append({"channel": "sms", "delivered": sms_ok, "message": message})

    insert_alert(run_id, "critical", message, "dashboard", True)
    emitted.append({"channel": "dashboard", "delivered": True, "message": message})
    return emitted


def _send_email(body: str) -> bool:
    if not Config.SMTP_HOST or not Config.ALERT_EMAIL_TO:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = "HackSphere Microgrid — Critical Overload"
        msg["From"] = Config.SMTP_FROM
        msg["To"] = Config.ALERT_EMAIL_TO
        msg.set_content(body)
        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=12) as smtp:
            smtp.starttls()
            if Config.SMTP_USER:
                smtp.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except Exception:
        return False


def _send_sms(body: str) -> bool:
    if not (Config.TWILIO_ACCOUNT_SID and Config.TWILIO_AUTH_TOKEN and Config.ALERT_SMS_TO):
        return False
    try:
        import urllib.parse
        import urllib.request

        url = f"https://api.twilio.com/2010-04-01/Accounts/{Config.TWILIO_ACCOUNT_SID}/Messages.json"
        data = urllib.parse.urlencode(
            {
                "From": Config.TWILIO_FROM,
                "To": Config.ALERT_SMS_TO,
                "Body": body[:1500],
            }
        ).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        credentials = f"{Config.TWILIO_ACCOUNT_SID}:{Config.TWILIO_AUTH_TOKEN}"
        import base64

        token = base64.b64encode(credentials.encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
        with urllib.request.urlopen(req, timeout=12) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False
