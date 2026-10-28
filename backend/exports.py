"""CSV and PDF export of the latest dispatch log."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from fpdf import FPDF

# The built-in PDF font only understands basic Latin letters.
# Symbols such as >=, arrows and long dashes must be swapped for plain text.
_REPLACE = {
    "\u2265": ">=", "\u2264": "<=", "\u2192": "->", "\u2190": "<-",
    "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2022": "*",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u00b0": " deg", "\u2026": "...", "\u00a0": " ",
}


def _safe(value) -> str:
    text = "" if value is None else str(value)
    for bad, good in _REPLACE.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


class ReportPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(20, 90, 70)
        self.cell(0, 8, "HackSphere 3.0  |  Microgrid Dispatch Report", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(90, 90, 90)
        self.cell(0, 6, datetime.now(timezone.utc).strftime("Generated %Y-%m-%d %H:%M UTC"), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")


def hours_to_csv(hours: list[dict]) -> bytes:
    buf = io.StringIO()
    fields = [
        "timestamp",
        "predicted_demand_kw",
        "grid_mode",
        "battery_soc_pct",
        "critical_overload",
        "reason",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in hours:
        writer.writerow(
            {
                "timestamp": row.get("timestamp", ""),
                "predicted_demand_kw": row.get("predicted_demand_kw", ""),
                "grid_mode": row.get("grid_mode", ""),
                "battery_soc_pct": row.get("battery_soc_pct", ""),
                "critical_overload": row.get("critical_overload", ""),
                "reason": row.get("reason", ""),
            }
        )
    return buf.getvalue().encode("utf-8")


def hours_to_pdf(hours: list[dict], summary: dict | None = None) -> bytes:
    pdf = ReportPDF(orientation="L", format="A4")
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_page()
    if summary:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(30, 30, 30)
        line = (
            f"Active mode: {summary.get('active_mode', '-')}   |   "
            f"Peak: {summary.get('peak_demand_kw', '-')} kW   |   "
            f"Mean: {summary.get('mean_demand_kw', '-')} kW   |   "
            f"Overload hours: {summary.get('overload_hours', 0)}   |   "
            f"Battery SOC: {summary.get('battery_soc_pct', '-')}%"
        )
        pdf.multi_cell(0, 6, _safe(line), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    headers = ["Timestamp", "Demand kW", "Grid mode", "SOC %", "Overload", "Reason"]
    widths = [42, 28, 52, 22, 24, 108]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(20, 90, 70)
    pdf.set_text_color(255, 255, 255)
    for w, h in zip(widths, headers):
        pdf.cell(w, 8, h, border=0, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(20, 20, 20)
    fill = False
    for row in hours:
        if fill:
            pdf.set_fill_color(240, 246, 242)
        else:
            pdf.set_fill_color(255, 255, 255)
        overload = "YES" if row.get("critical_overload") else "no"
        cells = [
            _safe(row.get("timestamp", "")),
            _safe(row.get("predicted_demand_kw", "")),
            _safe(row.get("grid_mode", "")),
            _safe(row.get("battery_soc_pct", "")),
            overload,
            _safe(row.get("reason", ""))[:70],
        ]
        for w, c in zip(widths, cells):
            pdf.cell(w, 7, c, border=0, fill=True)
        pdf.ln()
        fill = not fill
    return bytes(pdf.output())