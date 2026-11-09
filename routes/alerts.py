"""Alert inbox and lifecycle actions."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import db
from ..services import audit
from ..services.analytics import accessible_household_ids
from ..services.hashing import utc_now

alerts_bp = Blueprint("alerts", __name__, url_prefix="/alerts")


@alerts_bp.route("/")
@login_required
def index():
    ids = accessible_household_ids(current_user)
    if not ids:
        return render_template("alerts.html", alerts=[], households=[], filters={}, page_title="Alerts")
    placeholders = ",".join(["%s"] * len(ids))
    filters = {"severity": request.args.get("severity", ""), "status": request.args.get("status", "unread")}
    where = [f"a.household_id IN ({placeholders})"]
    params: list = list(ids)
    if filters["severity"] in ("critical", "warning", "info"):
        where.append("a.severity=%s")
        params.append(filters["severity"])
    if filters["status"] == "unread":
        where.append("a.is_read=0")
    elif filters["status"] == "resolved":
        where.append("a.resolved_at IS NOT NULL")
    rows = db.fetch_all(
        f"""SELECT a.*, h.household_code, h.name AS household_name
            FROM alerts a JOIN households h ON h.id=a.household_id
            WHERE {' AND '.join(where)} ORDER BY a.created_at DESC LIMIT 200""",
        params,
    )
    households = db.fetch_all(
        f"SELECT id, household_code, name FROM households WHERE id IN ({placeholders})",
        ids,
    )
    unread = db.fetch_value(
        f"SELECT COUNT(*) FROM alerts WHERE household_id IN ({placeholders}) AND is_read=0",
        ids,
        default=0,
    )
    return render_template(
        "alerts.html",
        alerts=rows,
        households=households,
        filters=filters,
        unread=int(unread or 0),
        page_title="Alerts",
    )


def _alert_for_user(alert_id: int) -> dict | None:
    alert = db.fetch_one(
        """SELECT a.*, h.user_id, h.household_code FROM alerts a
           JOIN households h ON h.id=a.household_id WHERE a.id=%s""",
        (alert_id,),
    )
    if not alert:
        return None
    if not current_user.is_admin and int(alert["user_id"] or 0) != current_user.id:
        return None
    return alert


@alerts_bp.route("/<int:alert_id>/read", methods=["POST"])
@login_required
def mark_read(alert_id: int):
    alert = _alert_for_user(alert_id)
    if not alert:
        abort(404)
    db.execute("UPDATE alerts SET is_read=1 WHERE id=%s", (alert_id,))
    audit.record("alert.read", entity="alert", entity_id=alert_id)
    return redirect(request.form.get("return_to") or url_for("alerts.index"))


@alerts_bp.route("/<int:alert_id>/resolve", methods=["POST"])
@login_required
def resolve(alert_id: int):
    alert = _alert_for_user(alert_id)
    if not alert:
        abort(404)
    db.execute("UPDATE alerts SET is_read=1, resolved_at=%s WHERE id=%s", (utc_now(), alert_id))
    audit.record("alert.resolve", entity="alert", entity_id=alert_id, details={"type": alert["alert_type"]})
    flash("Alert marked as resolved.", "success")
    return redirect(request.form.get("return_to") or url_for("alerts.index"))


@alerts_bp.route("/read-all", methods=["POST"])
@login_required
def read_all():
    ids = accessible_household_ids(current_user)
    if ids:
        placeholders = ",".join(["%s"] * len(ids))
        db.execute(f"UPDATE alerts SET is_read=1 WHERE household_id IN ({placeholders}) AND is_read=0", ids)
    audit.record("alert.read_all", entity="alert")
    flash("All visible alerts marked as read.", "success")
    return redirect(request.form.get("return_to") or url_for("alerts.index"))
