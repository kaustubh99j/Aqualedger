"""Household management for residents and administrators."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import db
from ..services import audit
from ..services.analytics import conservation_score, daily_series, get_household_or_none, monthly_series
from ..services.hashing import new_household_ref, utc_now
from ..services.settings import clear_household_limits, resolve_limits, set_household_limits
from ..utils import HOUSEHOLD_CODE_RE, ValidationError, parse_date, parse_float

households_bp = Blueprint("households", __name__, url_prefix="/households")


def _next_code() -> str:
    row = db.fetch_one(
        "SELECT household_code FROM households WHERE household_code LIKE 'AL-HH-%' ORDER BY id DESC LIMIT 1"
    )
    suffix = 0
    if row:
        try:
            suffix = int(row["household_code"].split("-")[-1])
        except ValueError:
            suffix = db.fetch_value("SELECT COUNT(*) FROM households", default=0) or 0
    return f"AL-HH-{suffix + 1:04d}"


def _validate_household_form(existing_id: int | None = None) -> dict:
    data = {
        "household_code": (request.form.get("household_code") or "").strip().upper(),
        "name": (request.form.get("name") or "").strip(),
        "address": (request.form.get("address") or "").strip(),
        "city": (request.form.get("city") or "Pune").strip(),
        "members_count": request.form.get("members_count") or "1",
        "meter_id": (request.form.get("meter_id") or "").strip().upper(),
        "meter_installed_on": (request.form.get("meter_installed_on") or "").strip(),
        "user_id": (request.form.get("user_id") or "").strip(),
    }
    if not data["household_code"]:
        data["household_code"] = _next_code()
    if not HOUSEHOLD_CODE_RE.match(data["household_code"]):
        raise ValidationError("Household code must be 3-32 characters: letters, numbers or dashes.")
    if len(data["name"]) < 2 or len(data["name"]) > 120:
        raise ValidationError("Household name must be 2-120 characters.")
    if len(data["address"]) < 5 or len(data["address"]) > 255:
        raise ValidationError("Address must be 5-255 characters.")
    if len(data["city"]) < 2 or len(data["city"]) > 80:
        raise ValidationError("City must be 2-80 characters.")
    try:
        members = int(data["members_count"])
    except ValueError as exc:
        raise ValidationError("Members count must be a whole number.") from exc
    if members < 1 or members > 50:
        raise ValidationError("Members count must be between 1 and 50.")
    data["members_count"] = members
    if len(data["meter_id"]) < 3 or len(data["meter_id"]) > 60:
        raise ValidationError("Meter ID must be 3-60 characters.")
    if data["meter_installed_on"]:
        data["meter_installed_on"] = parse_date(data["meter_installed_on"], "Meter installation date")
    else:
        data["meter_installed_on"] = None
    if data["user_id"]:
        user = db.fetch_one("SELECT id FROM users WHERE id=%s AND is_active=1", (data["user_id"],))
        if not user:
            raise ValidationError("Selected resident account does not exist.")
        data["user_id"] = int(data["user_id"])
    else:
        data["user_id"] = None

    duplicate = db.fetch_one(
        "SELECT id FROM households WHERE (household_code=%s OR meter_id=%s) AND id<>%s",
        (data["household_code"], data["meter_id"], existing_id or 0),
    )
    if duplicate:
        raise ValidationError("A household with this code or meter ID already exists.")
    return data


@households_bp.route("/")
@login_required
def index():
    if current_user.is_admin:
        households = db.fetch_all(
            """SELECT h.*, u.name AS owner_name, u.email AS owner_email,
                      (SELECT COUNT(*) FROM readings r WHERE r.household_id=h.id) AS reading_count,
                      (SELECT COALESCE(SUM(r.consumption_l),0) FROM readings r
                       WHERE r.household_id=h.id AND r.reading_date >= DATE_FORMAT(CURDATE(), '%%Y-%%m-01')) AS month_l
               FROM households h LEFT JOIN users u ON u.id=h.user_id
               ORDER BY h.household_code"""
        )
        users = db.fetch_all("SELECT id, name, email FROM users WHERE is_active=1 ORDER BY name")
    else:
        households = db.fetch_all(
            """SELECT h.*,
                      (SELECT COUNT(*) FROM readings r WHERE r.household_id=h.id) AS reading_count,
                      (SELECT COALESCE(SUM(r.consumption_l),0) FROM readings r
                       WHERE r.household_id=h.id AND r.reading_date >= DATE_FORMAT(CURDATE(), '%%Y-%%m-01')) AS month_l
               FROM households h WHERE h.user_id=%s ORDER BY h.household_code""",
            (current_user.id,),
        )
        users = []
    return render_template(
        "households.html", households=households, users=users, page_title="Households"
    )


@households_bp.route("/create", methods=["POST"])
@login_required
def create():
    if not current_user.is_admin:
        abort(403)
    try:
        data = _validate_household_form()
        household_id = db.execute(
            """INSERT INTO households (user_id, household_code, name, address, city, members_count,
                                       meter_id, meter_installed_on, chain_ref, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                data["user_id"],
                data["household_code"],
                data["name"],
                data["address"],
                data["city"],
                data["members_count"],
                data["meter_id"],
                data["meter_installed_on"],
                new_household_ref(),
                utc_now(),
            ),
        )
        audit.record("household.create", entity="household", entity_id=household_id, details=data)
        flash(f"Household {data['household_code']} created.", "success")
    except ValidationError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("households.index"))


@households_bp.route("/<int:household_id>/update", methods=["POST"])
@login_required
def update(household_id: int):
    if not current_user.is_admin:
        abort(403)
    household = db.fetch_one("SELECT * FROM households WHERE id=%s", (household_id,))
    if not household:
        abort(404)
    try:
        data = _validate_household_form(existing_id=household_id)
        db.execute(
            """UPDATE households SET user_id=%s, household_code=%s, name=%s, address=%s, city=%s,
                   members_count=%s, meter_id=%s, meter_installed_on=%s WHERE id=%s""",
            (
                data["user_id"],
                data["household_code"],
                data["name"],
                data["address"],
                data["city"],
                data["members_count"],
                data["meter_id"],
                data["meter_installed_on"],
                household_id,
            ),
        )
        # Changing household ownership requires the new owner to access existing readings.
        audit.record(
            "household.update",
            entity="household",
            entity_id=household_id,
            details={"before": {k: str(household.get(k)) for k in ("name", "user_id", "meter_id")}, "after": data},
        )
        flash("Household updated. Note: changing members affects the conservation baseline.", "success")
    except ValidationError as exc:
        flash(str(exc), "danger")
    return redirect(request.form.get("return_to") or url_for("households.index"))


@households_bp.route("/<int:household_id>/status", methods=["POST"])
@login_required
def toggle_status(household_id: int):
    if not current_user.is_admin:
        abort(403)
    household = db.fetch_one("SELECT * FROM households WHERE id=%s", (household_id,))
    if not household:
        abort(404)
    new_status = "inactive" if household["status"] == "active" else "active"
    db.execute("UPDATE households SET status=%s WHERE id=%s", (new_status, household_id))
    audit.record("household.status", entity="household", entity_id=household_id, details={"status": new_status})
    flash(f"Household marked {new_status}.", "success")
    return redirect(url_for("households.index"))


@households_bp.route("/<int:household_id>/delete", methods=["POST"])
@login_required
def delete(household_id: int):
    if not current_user.is_admin:
        abort(403)
    household = db.fetch_one(
        "SELECT h.*, (SELECT COUNT(*) FROM readings r WHERE r.household_id=h.id) AS readings FROM households h WHERE h.id=%s",
        (household_id,),
    )
    if not household:
        abort(404)
    if int(household["readings"] or 0) > 0:
        flash(
            "This household has meter readings and cannot be deleted. Mark it inactive instead to preserve the audit trail.",
            "danger",
        )
        return redirect(url_for("households.index"))
    db.execute("DELETE FROM households WHERE id=%s", (household_id,))
    audit.record("household.delete", entity="household", entity_id=household_id, details={"code": household["household_code"]})
    flash("Household deleted.", "success")
    return redirect(url_for("households.index"))


@households_bp.route("/<int:household_id>")
@login_required
def detail(household_id: int):
    household = get_household_or_none(household_id, current_user)
    if not household:
        abort(404)
    readings = db.fetch_all(
        """SELECT * FROM readings WHERE household_id=%s ORDER BY reading_date DESC, id DESC LIMIT 20""",
        (household_id,),
    )
    limits = resolve_limits(household_id)
    score = conservation_score(household)
    stats = db.fetch_one(
        """SELECT COALESCE(SUM(consumption_l),0) AS month_l,
                  COALESCE(AVG(consumption_l),0) AS avg_l,
                  COUNT(*) AS total_readings,
                  MAX(reading_date) AS last_reading
           FROM readings WHERE household_id=%s AND reading_date >= DATE_FORMAT(CURDATE(), '%%Y-%%m-01')""",
        (household_id,),
    ) or {}
    alerts = db.fetch_all(
        "SELECT * FROM alerts WHERE household_id=%s ORDER BY created_at DESC LIMIT 5", (household_id,)
    )
    daily = daily_series([household_id], 30)
    monthly = monthly_series([household_id], 12)
    latest = db.fetch_one(
        "SELECT * FROM readings WHERE household_id=%s ORDER BY reading_date DESC, id DESC LIMIT 1",
        (household_id,),
    )
    return render_template(
        "household_detail.html",
        household=household,
        readings=readings,
        limits=limits,
        score=score,
        stats=stats,
        alerts=alerts,
        daily=daily,
        monthly=monthly,
        latest=latest,
        page_title=household["household_code"],
    )


@households_bp.route("/<int:household_id>/limits", methods=["POST"])
@login_required
def update_limits(household_id: int):
    if not current_user.is_admin:
        abort(403)
    household = db.fetch_one("SELECT * FROM households WHERE id=%s", (household_id,))
    if not household:
        abort(404)
    try:
        daily = parse_float(request.form.get("daily_limit_l"), "Daily limit", minimum=1, maximum=100000)
        monthly = parse_float(request.form.get("monthly_limit_l"), "Monthly limit", minimum=1, maximum=3000000)
        threshold = int(request.form.get("alert_threshold_pct") or 80)
        if threshold < 10 or threshold > 100:
            raise ValidationError("Alert threshold must be between 10 and 100 percent.")
        if monthly < daily:
            raise ValidationError("Monthly limit must be greater than or equal to the daily limit.")
        set_household_limits(household_id, daily, monthly, threshold, current_user.id)
        audit.record("household.limits", entity="household", entity_id=household_id,
                     details={"daily": daily, "monthly": monthly, "threshold": threshold})
        flash("Household-specific limits saved.", "success")
    except (ValidationError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(request.form.get("return_to") or url_for("households.detail", household_id=household_id))


@households_bp.route("/<int:household_id>/limits/reset", methods=["POST"])
@login_required
def reset_limits(household_id: int):
    if not current_user.is_admin:
        abort(403)
    clear_household_limits(household_id)
    audit.record("household.limits_reset", entity="household", entity_id=household_id)
    flash("Household limits reset to the global defaults.", "info")
    return redirect(url_for("households.detail", household_id=household_id))
