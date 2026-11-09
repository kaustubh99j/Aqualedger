"""Resident dashboard, conservation score and profile management."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.security import check_password_hash, generate_password_hash

from .. import db
from ..services import audit
from ..services.analytics import accessible_household_ids, dashboard_stats, conservation_score
from ..services.hashing import utc_now
from ..services.settings import get_settings

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
@login_required
def index():
    household_ids = accessible_household_ids(current_user)
    stats = dashboard_stats(current_user)
    households = db.fetch_all(
        f"""SELECT h.*, u.name AS owner_name,
                   (SELECT COALESCE(SUM(r.consumption_l),0) FROM readings r
                    WHERE r.household_id=h.id AND r.reading_date >= DATE_FORMAT(CURDATE(), '%%Y-%%m-01')) AS month_l
            FROM households h LEFT JOIN users u ON u.id=h.user_id
            WHERE h.id IN ({','.join(['%s'] * len(household_ids))}) ORDER BY h.household_code"""
        if household_ids
        else "SELECT NULL AS id LIMIT 0",
        household_ids if household_ids else [],
    ) if household_ids else []
    alerts = db.fetch_all(
        f"""SELECT a.*, h.household_code FROM alerts a JOIN households h ON h.id=a.household_id
            WHERE h.id IN ({','.join(['%s'] * len(household_ids))}) AND a.is_read=0
            ORDER BY FIELD(a.severity,'critical','warning','info'), a.created_at DESC LIMIT 5"""
        if household_ids
        else "SELECT NULL AS id LIMIT 0",
        household_ids if household_ids else [],
    ) if household_ids else []
    recent = db.fetch_all(
        f"""SELECT r.*, h.household_code FROM readings r JOIN households h ON h.id=r.household_id
            WHERE r.household_id IN ({','.join(['%s'] * len(household_ids))})
            ORDER BY r.reading_date DESC, r.id DESC LIMIT 8"""
        if household_ids
        else "SELECT NULL AS id LIMIT 0",
        household_ids if household_ids else [],
    ) if household_ids else []
    scores = [
        {"household": household, "score": conservation_score(household)}
        for household in households
    ]
    for item in scores:
        limits = db.fetch_one("SELECT * FROM household_limits WHERE household_id=%s", (item["household"]["id"],))
        item["override"] = bool(limits)
    settings = get_settings()
    return render_template(
        "dashboard.html",
        stats=stats,
        households=households,
        alerts=alerts,
        recent=recent,
        scores=scores,
        settings=settings,
        page_title="Dashboard",
    )


@dashboard_bp.route("/profile")
@login_required
def profile():
    household_ids = accessible_household_ids(current_user)
    households = db.fetch_all(
        "SELECT * FROM households WHERE user_id=%s ORDER BY household_code", (current_user.id,)
    )
    linked = db.fetch_one(
        "SELECT * FROM wallet_authorizations WHERE user_id=%s", (current_user.id,)
    )
    return render_template(
        "profile.html",
        households=households,
        linked_authorization=linked,
        wallet_linked=bool(current_user.wallet_address),
        page_title="My profile",
    )


@dashboard_bp.route("/profile/update", methods=["POST"])
@login_required
def update_profile():
    name = (request.form.get("name") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    if len(name) < 2 or len(name) > 120:
        flash("Full name must be between 2 and 120 characters.", "danger")
        return redirect(url_for("dashboard.profile"))
    if phone and (not phone.replace("+", "").replace("-", "").replace(" ", "").isdigit() or len(phone) > 30):
        flash("Phone number format is invalid.", "danger")
        return redirect(url_for("dashboard.profile"))
    db.execute("UPDATE users SET name=%s, phone=%s WHERE id=%s", (name, phone or None, current_user.id))
    audit.record("profile.update", entity="user", entity_id=current_user.id, details={"name": name})
    flash("Profile updated.", "success")
    return redirect(url_for("dashboard.profile"))


@dashboard_bp.route("/profile/password", methods=["POST"])
@login_required
def change_password():
    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""
    row = db.fetch_one("SELECT password_hash FROM users WHERE id=%s", (current_user.id,))
    if not row or not check_password_hash(row["password_hash"], current):
        flash("Current password is incorrect.", "danger")
        return redirect(url_for("dashboard.profile"))
    if len(new) < 8 or not any(ch.isalpha() for ch in new) or not any(ch.isdigit() for ch in new):
        flash("New password must be at least 8 characters and contain a letter and a number.", "danger")
        return redirect(url_for("dashboard.profile"))
    if new != confirm:
        flash("New passwords do not match.", "danger")
        return redirect(url_for("dashboard.profile"))
    if new == current:
        flash("Choose a password different from your current one.", "warning")
        return redirect(url_for("dashboard.profile"))
    db.execute(
        "UPDATE users SET password_hash=%s WHERE id=%s",
        (generate_password_hash(new, method="pbkdf2:sha256:600000"), current_user.id),
    )
    audit.record("profile.password_change", entity="user", entity_id=current_user.id)
    flash("Password changed successfully.", "success")
    return redirect(url_for("dashboard.profile"))
