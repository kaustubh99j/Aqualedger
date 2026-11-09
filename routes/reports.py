"""Historical reports, conservation scorecards and CSV export."""
from __future__ import annotations

import csv
import io
from datetime import date

from flask import Blueprint, Response, abort, render_template, request
from flask_login import current_user, login_required

from .. import db
from ..services.analytics import (
    accessible_household_ids,
    conservation_score,
    monthly_report,
    score_summary,
)
from ..services.settings import get_settings, resolve_limits

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def _context():
    all_ids = accessible_household_ids(current_user)
    ids = all_ids
    raw_household = request.args.get("household_id", "")
    selected = None
    if raw_household.isdigit():
        hid = int(raw_household)
        if hid in ids:
            selected = hid
            ids = [hid]
        else:
            abort(404)
    try:
        year = int(request.args.get("year", date.today().year))
    except ValueError:
        year = date.today().year
    if year < 2000 or year > date.today().year:
        year = date.today().year
    households = db.fetch_all(
        f"""SELECT id, household_code, name FROM households
            WHERE id IN ({','.join(['%s'] * len(all_ids))}) ORDER BY household_code"""
        if all_ids
        else "SELECT NULL AS id LIMIT 0",
        all_ids if all_ids else [],
    )
    rows = monthly_report(ids, year)
    total_l = round(sum(row["total_l"] for row in rows), 2)
    total_cost = round(sum(row["cost"] for row in rows), 2)
    # A more meaningful average: total / number of months that contain data.
    months_with_data = [row for row in rows if row["entries"]]
    avg_month_l = round(total_l / len(months_with_data), 1) if months_with_data else 0
    settings = get_settings()
    monthly_limit = 0.0
    for household_id in ids:
        monthly_limit += resolve_limits(household_id)["monthly_limit_l"]
    scores = []
    for household_id in ids:
        household = db.fetch_one("SELECT * FROM households WHERE id=%s", (household_id,))
        if household:
            scores.append({"household": household, "score": conservation_score(household)})
    return {
        "rows": rows,
        "year": year,
        "selected_household": selected,
        "households": households,
        "total_l": total_l,
        "total_cost": total_cost,
        "avg_month_l": avg_month_l,
        "monthly_limit": round(monthly_limit, 2),
        "score": score_summary(ids),
        "scores": scores,
        "settings": settings,
        "household_ids": ids,
    }


@reports_bp.route("/")
@login_required
def index():
    context = _context()
    return render_template("reports.html", **context, page_title="Reports")


@reports_bp.route("/export.csv")
@login_required
def export_csv():
    context = _context()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["AquaLedger monthly consumption report"])
    writer.writerow(["Year", context["year"]])
    writer.writerow(["Household filter", context["selected_household"] or "All accessible households"])
    writer.writerow([])
    writer.writerow(["Month", "Total litres", "Avg litres/day", "Entries", "Monthly limit (L)",
                     "Percent of limit", "Estimated cost (INR)", "Status"])
    for row in context["rows"]:
        writer.writerow([
            row["month"], row["total_l"], row["avg_daily_l"], row["entries"], row["limit_l"],
            row["pct_of_limit"], row["cost"], row["status"],
        ])
    writer.writerow([])
    writer.writerow(["Total litres", context["total_l"]])
    writer.writerow(["Estimated cost (INR)", context["total_cost"]])
    writer.writerow(["Conservation score", context["score"]["score"] if context["score"]["score"] is not None else ""])
    writer.writerow([])
    writer.writerow(["Record digest algorithm", "keccak256(AquaLedger:v1|householdRef|sequence|meterId|readingDate|previousM3|currentM3|consumptionL|createdAt)"])
    output.seek(0)
    filename = f"aqualedger-report-{context['year']}.csv"
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
