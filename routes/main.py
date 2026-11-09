"""Public landing page and shareable information."""
from __future__ import annotations

from flask import Blueprint, render_template

from .. import db

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def landing():
    stats = {
        "households": 0,
        "readings": 0,
        "litres": 0,
        "anchored": 0,
        "score": None,
    }
    try:
        row = db.fetch_one(
            """SELECT
                 (SELECT COUNT(*) FROM households WHERE status='active') AS households,
                 (SELECT COUNT(*) FROM readings) AS readings,
                 (SELECT COALESCE(SUM(consumption_l),0) FROM readings) AS litres,
                 (SELECT COUNT(*) FROM readings WHERE status='anchored') AS anchored"""
        )
        if row:
            stats.update(
                households=int(row["households"] or 0),
                readings=int(row["readings"] or 0),
                litres=float(row["litres"] or 0),
                anchored=int(row["anchored"] or 0),
            )
        stats["score"] = db.fetch_value(
            "SELECT ROUND(AVG(consumption_l)) FROM readings WHERE reading_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)"
        )
    except Exception:
        pass
    return render_template("landing.html", stats=stats)
