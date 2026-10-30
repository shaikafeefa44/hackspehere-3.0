"""Wattcast AI Dispatch Assistant.

Provides rule-based and optional LLM answers for 24-hour smart microgrid energy demand,
renewable storage vs main-grid dispatch decisions, battery state of charge, and overload risks.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

from backend.config import Config
from backend.grid import summarize

logger = logging.getLogger(__name__)


def _extract_hour(text: str) -> int | None:
    """Extract an hour (0-23) from a natural language query if present."""
    q = text.lower()

    # 1. Matches "6:00 pm", "6:00pm", "6 pm", "6pm", "6:00 am", "6 am", "12 pm", etc.
    match_12 = re.search(r"\b(?:at\s+|hour\s+)?(\d{1,2})(?::00)?\s*(am|pm)\b", q)
    if match_12:
        h = int(match_12.group(1))
        meridiem = match_12.group(2)
        if 1 <= h <= 12:
            if meridiem == "pm":
                return 12 if h == 12 else h + 12
            else:
                return 0 if h == 12 else h

    # 2. Matches "18:00" or "at 18:00"
    match_24 = re.search(r"\b(?:at\s+|hour\s+)?(\d{1,2}):00\b", q)
    if match_24:
        h = int(match_24.group(1))
        if 0 <= h <= 23:
            return h

    # 3. Matches "hour 18", "hour 6", "h 18"
    match_hour = re.search(r"\b(?:hour|hr)\s+(\d{1,2})\b", q)
    if match_hour:
        h = int(match_hour.group(1))
        if 0 <= h <= 23:
            return h

    # 4. Matches "at 18"
    match_at = re.search(r"\bat\s+(\d{1,2})\b", q)
    if match_at:
        h = int(match_at.group(1))
        if 0 <= h <= 23:
            return h

    # 5. Words like midnight / noon
    if "midnight" in q:
        return 0
    if "noon" in q:
        return 12

    return None


def _format_hour_12(hour: int) -> str:
    """Format hour integer into readable 12-hour / 24-hour string."""
    h12 = hour % 12 or 12
    ampm = "PM" if hour >= 12 else "AM"
    return f"{hour:02d}:00 ({h12} {ampm})"


def _find_hour_record(hours: list[dict], target_hour: int) -> dict | None:
    for h in hours:
        hour_val = h.get("hour")
        if hour_val is not None and int(hour_val) == target_hour:
            return h
        ts = str(h.get("timestamp", ""))
        if len(ts) >= 13 and ts[11:13].isdigit() and int(ts[11:13]) == target_hour:
            return h
    return None


def _default_suggestions(hours: list[dict], summary: dict) -> list[str]:
    if not hours:
        return [
            "What are the dispatch rules?",
            "How does battery storage work?",
            "What triggers an overload alert?",
        ]
    overloads = summary.get("overload_hours", 0)
    suggestions = ["What is the peak hour?", "Why was storage used?", "Battery status"]
    if overloads > 0:
        suggestions.insert(0, "Show overload hours")
    else:
        suggestions.append("Forecast summary")
    return suggestions[:4]


def _answer_specific_hour(target_hour: int, hours: list[dict]) -> tuple[str, list[str]]:
    h_str = _format_hour_12(target_hour)
    if not hours:
        return (
            f"No forecast data is currently loaded to inspect {h_str}. "
            "Please run a sample day or upload telemetry first.",
            ["What are the dispatch rules?", "Tell me about the battery", "How do overload alerts work?"],
        )

    rec = _find_hour_record(hours, target_hour)
    if not rec:
        return (
            f"Hour {h_str} was not found in the current 24-hour forecast window.",
            ["What is the peak hour?", "Forecast summary", "Why was storage used?"],
        )

    demand = float(rec.get("predicted_demand_kw", 0.0))
    mode = rec.get("grid_mode", "Main Grid Mode")
    soc = float(rec.get("battery_soc_pct", 0.0))
    reason = rec.get("reason", "Standard dispatch")
    overload = bool(rec.get("critical_overload") or demand >= 200.0)

    lines = [
        f"Forecast for {h_str} ({rec.get('timestamp', '')}):",
        f"• Predicted Demand: {demand:.1f} kW",
        f"• Grid Mode: {mode}",
        f"• Battery SOC: {soc:.1f}%",
        f"• Dispatch Reason: {reason}",
    ]
    if overload:
        lines.append("• ⚠️ CRITICAL OVERLOAD: Demand is at or above 200 kW! Operator attention required.")

    suggestions = ["What is the peak hour?", "Why was storage used?", "Show overload hours"]
    return "\n".join(lines), suggestions


def _answer_peak_hour(hours: list[dict], summary: dict) -> tuple[str, list[str]]:
    if not hours:
        return (
            "No forecast data is currently loaded. When a forecast is active, "
            "I will identify the 24-hour peak demand, its timestamp, and the dispatch mode.",
            ["What are the dispatch rules?", "Tell me about the battery", "What triggers overload?"],
        )

    peak_rec = max(hours, key=lambda h: float(h.get("predicted_demand_kw", 0.0)))
    peak_val = float(peak_rec.get("predicted_demand_kw", 0.0))
    peak_h = peak_rec.get("hour")
    if peak_h is None and peak_rec.get("timestamp"):
        try:
            peak_h = int(str(peak_rec["timestamp"])[11:13])
        except Exception:
            peak_h = 0
    peak_h_fmt = _format_hour_12(int(peak_h or 0))
    peak_ts = peak_rec.get("timestamp", "")
    peak_mode = peak_rec.get("grid_mode", "Renewable Storage Mode")
    peak_reason = peak_rec.get("reason", "")
    is_overload = bool(peak_rec.get("critical_overload") or peak_val >= 200.0)

    lines = [
        f"Peak demand is {peak_val:.1f} kW, predicted at {peak_h_fmt} ({peak_ts}).",
        f"• Dispatch Mode: {peak_mode}",
        f"• Dispatch Reason: {peak_reason}",
    ]
    if is_overload:
        lines.append("• ⚠️ Alert: This peak reaches or exceeds the 200 kW critical overload threshold.")
    else:
        lines.append("• Status: Safe operating range (below 200 kW critical limit).")

    suggestions = ["Show overload hours", "Why was storage used?", "Battery status"]
    return "\n".join(lines), suggestions


def _answer_overload_hours(hours: list[dict], summary: dict) -> tuple[str, list[str]]:
    if not hours:
        return (
            "No forecast data is currently loaded. Under Wattcast rules, any hour "
            "where predicted demand reaches or exceeds 200 kW triggers a Critical Overload Alert.",
            ["What are the dispatch rules?", "Tell me about the battery", "How do overload alerts work?"],
        )

    overloads = [h for h in hours if h.get("critical_overload") or float(h.get("predicted_demand_kw", 0.0)) >= 200.0]
    if overloads:
        lines = [f"⚠️ Critical Overload Alert: {len(overloads)} hour(s) reach or exceed the 200 kW threshold:"]
        for o in overloads:
            h = int(o.get("hour") if o.get("hour") is not None else 0)
            kw = float(o.get("predicted_demand_kw", 0.0))
            ts = o.get("timestamp", "")
            reason = o.get("reason", "")
            lines.append(f"• {_format_hour_12(h)} ({ts}): {kw:.1f} kW [{reason}]")
        lines.append("Immediate operator load-shedding or generation backup is recommended during these periods.")
        return "\n".join(lines), ["Show recommendations", "What is the peak hour?", "Battery status"]
    else:
        ans = (
            "Clear: No critical overloads (>= 200 kW) are predicted in this 24-hour forecast. "
            f"Peak demand is {summary.get('peak_demand_kw', 0):.1f} kW, staying within safe operating capacity."
        )
        return ans, ["What is the peak hour?", "Why was storage used?", "Forecast summary"]


def _answer_why_storage(hours: list[dict], summary: dict) -> tuple[str, list[str]]:
    explanation = (
        "Under Wattcast dispatch rules, Renewable Storage Mode is activated whenever:\n"
        "1. Predicted demand > 160 kW (high demand threshold), OR\n"
        "2. The time falls within the evening peak window (18:00–22:00 / 6 PM–10 PM), regardless of demand.\n\n"
        "In all other periods (demand <= 160 kW and outside 18:00–22:00), Main Grid Mode is selected "
        "and trickle-charges the battery at 45 kW."
    )
    if not hours:
        return explanation, ["How does battery storage work?", "What are the overload rules?", "Run sample day"]

    storage_hours = [h for h in hours if "Renewable" in str(h.get("grid_mode", ""))]
    evening_count = sum(1 for h in storage_hours if int(h.get("hour", 0)) in range(18, 23))
    demand_count = sum(
        1 for h in storage_hours
        if float(h.get("predicted_demand_kw", 0.0)) > 160.0 and int(h.get("hour", 0)) not in range(18, 23)
    )

    details = (
        f"\n\nIn the current forecast, Renewable Storage is active for {len(storage_hours)} hour(s):\n"
        f"• Evening window (18:00–22:00): {evening_count} hour(s)\n"
        f"• Daytime demand > 160 kW: {demand_count} hour(s)"
    )
    suggestions = ["Battery status", "What is the peak hour?", "Show overload hours"]
    return explanation + details, suggestions


def _answer_battery(hours: list[dict], summary: dict) -> tuple[str, list[str]]:
    if not hours:
        ans = (
            "Battery Storage System Specifications:\n"
            "• Capacity: 800 kWh simulated storage bank\n"
            "• Nominal Initial SOC: 72%\n"
            "• Safe Operating Range: 10% to 95% State of Charge\n"
            "• Dispatch Dynamics: Discharges to supply microgrid load during Renewable Storage hours; "
            "trickle-charges at 45 kW during Main Grid hours."
        )
        return ans, ["What are the dispatch rules?", "What triggers overload?", "Run sample day"]

    soc_end = float(summary.get("battery_soc_pct") or hours[-1].get("battery_soc_pct", 0.0))
    socs = [float(h.get("battery_soc_pct", 0.0)) for h in hours]
    min_soc = min(socs)
    max_soc = max(socs)
    renewable_hours = sum(1 for h in hours if "Renewable" in str(h.get("grid_mode", "")))

    lines = [
        "Battery Storage System Status (800 kWh Bank):",
        f"• Current / Final SOC: {soc_end:.1f}%",
        f"• 24-Hour SOC Range: {min_soc:.1f}% (min) to {max_soc:.1f}% (max)",
        f"• Storage Activity: Discharging during {renewable_hours} Renewable Storage hour(s); "
        f"trickle-charging at 45 kW during {len(hours) - renewable_hours} Main Grid hour(s).",
    ]
    if min_soc < 20.0:
        lines.append(f"• ⚠️ Note: Battery reaches a low reserve of {min_soc:.1f}%. Consider monitoring grid stability.")
    else:
        lines.append("• Status: Battery reserve remains healthy throughout the forecast period.")

    suggestions = ["Why was storage used?", "Forecast summary", "Show recommendations"]
    return "\n".join(lines), suggestions


def _answer_recommendations(hours: list[dict], summary: dict) -> tuple[str, list[str]]:
    if not hours:
        return (
            "No forecast data is currently loaded. Upload telemetry or click 'Run sample day' "
            "to generate microgrid dispatch insights and operational recommendations.",
            ["What are the dispatch rules?", "Tell me about the battery", "How do overload alerts work?"],
        )

    overloads = [h for h in hours if h.get("critical_overload") or float(h.get("predicted_demand_kw", 0.0)) >= 200.0]
    storage_count = sum(1 for h in hours if "Renewable" in str(h.get("grid_mode", "")))
    socs = [float(h.get("battery_soc_pct", 0.0)) for h in hours]
    min_soc = min(socs) if socs else 72.0

    lines = ["Wattcast Operational Recommendations:"]
    if overloads:
        lines.append(
            f"1. ⚠️ Overload Mitigation: {len(overloads)} critical hour(s) exceed 200 kW. "
            "Stage non-critical load shedding or bring auxiliary generators online before peak hours."
        )
    else:
        lines.append("1. Overload Margin: Demand is safely below the 200 kW limit across all 24 hours.")

    if min_soc < 25.0:
        lines.append(
            f"2. Battery Reserve Preservation: Minimum SOC drops to {min_soc:.1f}%. "
            "Ensure full trickle charging during midday off-peak windows."
        )
    else:
        lines.append(f"2. Battery State: Battery reserve is healthy (minimum {min_soc:.1f}% SOC).")

    if storage_count > 0:
        lines.append(
            f"3. Storage Dispatch: {storage_count} hours of Renewable Storage dispatch protect the main grid "
            "from high-tariff evening peak strain."
        )
    else:
        lines.append("3. Grid Dispatch: Continuous Main Grid operation provides steady baseload charging.")

    suggestions = ["Show overload hours", "Battery status", "Forecast summary"]
    return "\n".join(lines), suggestions


def _answer_summary(hours: list[dict], summary: dict) -> tuple[str, list[str]]:
    if not hours:
        return (
            "No forecast is currently loaded. Click 'Run sample day' in Overview or upload "
            "a telemetry CSV in 'Add data' to view a 24-hour demand forecast and dispatch plan.",
            ["What are the dispatch rules?", "Tell me about the battery", "What triggers overload?"],
        )

    active_mode = summary.get("active_mode", "Main Grid Mode")
    peak_demand = float(summary.get("peak_demand_kw", 0.0))
    mean_demand = float(summary.get("mean_demand_kw", 0.0))
    overload_hrs = int(summary.get("overload_hours", 0))
    renewable_hrs = int(summary.get("renewable_hours", 0))
    main_hrs = int(summary.get("main_grid_hours", 0))
    soc = float(summary.get("battery_soc_pct", 0.0))

    overload_text = f"⚠️ {overload_hrs} hour(s) >= 200 kW" if overload_hrs > 0 else "Clear (0 hours >= 200 kW)"

    lines = [
        "24-Hour Microgrid Forecast Summary:",
        f"• Active Mode: {active_mode}",
        f"• Peak Demand: {peak_demand:.1f} kW | Mean Demand: {mean_demand:.1f} kW",
        f"• Dispatch Allocation: {renewable_hrs}h Renewable Storage, {main_hrs}h Main Grid",
        f"• Critical Overload: {overload_text}",
        f"• Projected Battery SOC: {soc:.1f}%",
    ]
    suggestions = ["What is the peak hour?", "Why was storage used?", "Show overload hours", "Show recommendations"]
    return "\n".join(lines), suggestions


def _fallback(hours: list[dict]) -> tuple[str, list[str]]:
    text = (
        "I am Wattcast AI, your smart microgrid dispatch assistant. I can help you with:\n"
        "• Forecast Summary: 24-hour demand, peak, and mean load statistics.\n"
        "• Peak Hour: Identify maximum demand and timing ('when is the peak?').\n"
        "• Overload Watch: Hours reaching or exceeding 200 kW ('are there overloads?').\n"
        "• Dispatch Logic: Why Renewable Storage vs Main Grid was used ('why storage?').\n"
        "• Specific Hour Inspection: Detailed status for any hour ('why at 6 pm', 'hour 14').\n"
        "• Battery SOC: Storage capacity and state of charge ('battery status').\n"
        "• Recommendations: Actionable grid management advice ('recommendations')."
    )
    suggestions = [
        "Forecast summary",
        "What is the peak hour?",
        "Why was storage used?",
        "Battery status",
    ]
    return text, suggestions


def _rule_based_answer(question: str, data: dict | None) -> dict[str, Any]:
    hours = (data.get("hours") if isinstance(data, dict) else None) or []
    summary = (data.get("summary") if isinstance(data, dict) else None) or {}
    if not summary and hours:
        summary = summarize(hours)

    q = (question or "").strip().lower()

    # 1. Specific hour query (e.g. "why at 6 pm", "why at 18:00", "what happens at 8 am", "hour 14")
    target_hour = _extract_hour(q)
    if target_hour is not None and any(w in q for w in ["why", "what", "hour", "hr", "at", "demand", "mode", "pm", "am", ":00", "state"]):
        ans, sug = _answer_specific_hour(target_hour, hours)
        return {"answer": ans, "source": "rule", "suggestions": sug}

    # 2. Peak hour
    if any(w in q for w in ["peak", "maximum", "max load", "max demand", "highest demand", "highest load", "top demand"]):
        ans, sug = _answer_peak_hour(hours, summary)
        return {"answer": ans, "source": "rule", "suggestions": sug}

    # 3. Overload hours
    if any(w in q for w in ["overload", "critical", "200 kw", "exceed 200", "emergency"]):
        ans, sug = _answer_overload_hours(hours, summary)
        return {"answer": ans, "source": "rule", "suggestions": sug}

    # 4. Why storage was used
    if (
        any(w in q for w in [
            "why storage",
            "storage used",
            "renewable storage",
            "switch to storage",
            "why battery",
            "storage mode",
            "dispatch rule",
            "dispatch rules",
            "transition rule",
        ])
        or (("storage" in q or "renewable" in q) and ("why" in q or "rule" in q or "when" in q or "reason" in q))
    ):
        ans, sug = _answer_why_storage(hours, summary)
        return {"answer": ans, "source": "rule", "suggestions": sug}

    # 5. Battery
    if any(w in q for w in ["battery", "soc", "state of charge", "storage level", "storage capacity", "discharge"]):
        ans, sug = _answer_battery(hours, summary)
        return {"answer": ans, "source": "rule", "suggestions": sug}

    # 6. Recommendations
    if any(w in q for w in ["recommend", "recommendation", "recommendations", "suggest", "suggestion", "advice", "action", "actions", "what should i do"]):
        ans, sug = _answer_recommendations(hours, summary)
        return {"answer": ans, "source": "rule", "suggestions": sug}

    # 7. Summary
    if any(w in q for w in ["summary", "overview", "status", "report", "forecast", "how is", "how are", "state"]):
        ans, sug = _answer_summary(hours, summary)
        return {"answer": ans, "source": "rule", "suggestions": sug}

    # 8. Fallback
    ans, sug = _fallback(hours)
    return {"answer": ans, "source": "rule", "suggestions": sug}


def _ask_llm(question: str, data: dict | None, endpoint: str, token: str) -> str | None:
    """Send question + forecast context to LLM endpoint. Returns response string or None on failure."""
    try:
        hours = (data.get("hours") if isinstance(data, dict) else None) or []
        summary = (data.get("summary") if isinstance(data, dict) else None) or {}
        if not summary and hours:
            summary = summarize(hours)

        context_lines = []
        if hours and summary:
            context_lines.append(f"Forecast coverage: {len(hours)} hours")
            context_lines.append(f"Active grid mode: {summary.get('active_mode', 'Main Grid Mode')}")
            context_lines.append(f"Peak demand: {summary.get('peak_demand_kw', 0)} kW")
            context_lines.append(f"Mean demand: {summary.get('mean_demand_kw', 0)} kW")
            context_lines.append(f"Overload hours (>= 200 kW): {summary.get('overload_hours', 0)}")
            context_lines.append(f"Renewable storage hours: {summary.get('renewable_hours', 0)}")
            context_lines.append(f"Final Battery SOC: {summary.get('battery_soc_pct', 0)}%")
        else:
            context_lines.append("No active forecast run loaded.")

        system_prompt = (
            "You are Wattcast AI, an intelligent microgrid energy dispatch assistant.\n"
            "System Dispatch Rules:\n"
            "1. Renewable Storage Mode is used when predicted demand > 160 kW OR hour is 18:00–22:00 (evening peak window).\n"
            "2. Main Grid Mode is used otherwise (demand <= 160 kW and outside 18:00–22:00) and trickle-charges battery at 45 kW.\n"
            "3. Critical Overload Alert is triggered if demand >= 200 kW.\n"
            "Battery: 800 kWh capacity, initial 72% SOC, safe limits 10%–95%.\n"
            "Forecast context:\n" + "\n".join(context_lines) + "\n\n"
            "Provide concise, professional operational answers to the user."
        )

        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            "max_tokens": 400,
            "temperature": 0.3,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        resp = requests.post(endpoint, json=payload, headers=headers, timeout=5)
        if resp.status_code == 200:
            res_json = resp.json()
            if isinstance(res_json, dict):
                # Standard OpenAI format
                choices = res_json.get("choices")
                if isinstance(choices, list) and choices:
                    c0 = choices[0]
                    if isinstance(c0, dict):
                        msg = c0.get("message")
                        if isinstance(msg, dict) and msg.get("content"):
                            return str(msg["content"]).strip()
                        if c0.get("text"):
                            return str(c0["text"]).strip()
                # Generic formats
                if "answer" in res_json and isinstance(res_json["answer"], str):
                    return res_json["answer"].strip()
                if "response" in res_json and isinstance(res_json["response"], str):
                    return res_json["response"].strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM request failed, falling back to rule-based answers: %s", exc)
        return None

    return None


def answer_question(question: str, data: dict | None = None) -> dict[str, Any]:
    """Answer an energy dispatch question using optional LLM or robust rule engine."""
    q = (question or "").strip()
    endpoint = (os.getenv("LLM_ENDPOINT") or getattr(Config, "LLM_ENDPOINT", "")).strip()
    token = (os.getenv("LLM_AUTH_TOKEN") or getattr(Config, "LLM_AUTH_TOKEN", "")).strip()

    if endpoint and token:
        try:
            llm_text = _ask_llm(q, data, endpoint, token)
            if llm_text:
                hours = (data.get("hours") if isinstance(data, dict) else None) or []
                summary = (data.get("summary") if isinstance(data, dict) else None) or {}
                if not summary and hours:
                    summary = summarize(hours)
                suggestions = _default_suggestions(hours, summary)
                return {
                    "answer": llm_text,
                    "source": "llm",
                    "suggestions": suggestions,
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM handling encountered error, falling back to rules: %s", exc)

    return _rule_based_answer(q, data)
