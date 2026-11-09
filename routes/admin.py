"""Administrator dashboard, user management, settings and integrity tools."""
from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.security import generate_password_hash

from .. import db
from ..security import admin_required
from ..services import audit
from ..services.analytics import audit_readings, conservation_score, score_summary
from ..services.chain import chain
from ..services.hashing import compute_record_hash, utc_now
from ..services.settings import get_settings, update_settings
from ..utils import EMAIL_RE, ValidationError, parse_float

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/")
@login_required
@admin_required
def index():
    counts = db.fetch_one(
        """SELECT
             (SELECT COUNT(*) FROM users) AS users,
             (SELECT COUNT(*) FROM users WHERE role='admin') AS admins,
             (SELECT COUNT(*) FROM households) AS households,
             (SELECT COUNT(*) FROM households WHERE status='active') AS active_households,
             (SELECT COUNT(*) FROM readings) AS readings,
             (SELECT COUNT(*) FROM readings WHERE status='anchored') AS anchored,
             (SELECT COUNT(*) FROM readings WHERE status='pending') AS pending,
             (SELECT COUNT(*) FROM readings WHERE status='failed') AS failed,
             (SELECT COUNT(*) FROM readings WHERE status='tampered') AS tampered,
             (SELECT COUNT(*) FROM alerts WHERE is_read=0) AS unread_alerts,
             (SELECT COALESCE(SUM(consumption_l),0) FROM readings
              WHERE reading_date >= DATE_FORMAT(CURDATE(), '%Y-%m-01')) AS month_l"""
    ) or {}
    top = db.fetch_all(
        """SELECT h.id, h.household_code, h.name, COALESCE(SUM(r.consumption_l),0) AS month_l
           FROM households h LEFT JOIN readings r ON r.household_id=h.id
             AND r.reading_date >= DATE_FORMAT(CURDATE(), '%Y-%m-01')
           GROUP BY h.id ORDER BY month_l DESC LIMIT 5"""
    )
    recent_audit = db.fetch_all(
        """SELECT a.*, u.name AS user_name FROM audit_log a LEFT JOIN users u ON u.id=a.user_id
           ORDER BY a.id DESC LIMIT 10"""
    )
    recent_alerts = db.fetch_all(
        """SELECT a.*, h.household_code FROM alerts a JOIN households h ON h.id=a.household_id
           ORDER BY a.created_at DESC LIMIT 6"""
    )
    integrity = audit_readings(current_user, verify_chain=False, limit=300)
    status = chain.status()
    household_ids = [row["id"] for row in db.fetch_all("SELECT id FROM households WHERE status='active'")]
    return render_template(
        "admin/dashboard.html",
        counts=counts,
        top=top,
        recent_audit=recent_audit,
        recent_alerts=recent_alerts,
        integrity=integrity["summary"],
        chain_status=status,
        score=score_summary(household_ids),
        page_title="Admin overview",
    )


@admin_bp.route("/users")
@login_required
@admin_required
def users():
    rows = db.fetch_all(
        """SELECT u.id, u.name, u.email, u.role, u.phone, u.is_active, u.wallet_address,
                  u.wallet_verified, u.last_login, u.created_at,
                  (SELECT COUNT(*) FROM households h WHERE h.user_id=u.id) AS household_count,
                  (SELECT COUNT(*) FROM readings r JOIN households h ON h.id=r.household_id
                   WHERE h.user_id=u.id) AS reading_count,
                  wa.authorized AS wallet_authorized, wa.chain_mode AS auth_mode, wa.tx_hash AS auth_tx
           FROM users u LEFT JOIN wallet_authorizations wa ON wa.user_id=u.id
           ORDER BY u.role DESC, u.name"""
    )
    return render_template(
        "admin/users.html", users=rows, chain_mode=chain.mode, page_title="Users & wallets"
    )


@admin_bp.route("/users/create", methods=["POST"])
@login_required
@admin_required
def create_user():
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    phone = (request.form.get("phone") or "").strip()
    role = request.form.get("role") if request.form.get("role") in ("admin", "user") else "user"
    password = request.form.get("password") or ""
    errors = []
    if len(name) < 2 or len(name) > 120:
        errors.append("Name must be 2-120 characters.")
    if not EMAIL_RE.match(email):
        errors.append("Enter a valid email address.")
    if len(password) < 8 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        errors.append("Password must be at least 8 characters and contain a letter and a number.")
    if db.fetch_one("SELECT id FROM users WHERE email=%s", (email,)):
        errors.append("Email is already registered.")
    if errors:
        for error in errors:
            flash(error, "danger")
        return redirect(url_for("admin.users"))
    user_id = db.execute(
        """INSERT INTO users (name, email, password_hash, role, phone, created_at)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (name, email, generate_password_hash(password, method="pbkdf2:sha256:600000"), role, phone or None, utc_now()),
    )
    audit.record("admin.user_create", entity="user", entity_id=user_id, details={"email": email, "role": role})
    flash(f"User {name} created.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/status", methods=["POST"])
@login_required
@admin_required
def toggle_user(user_id: int):
    if user_id == current_user.id:
        flash("You cannot deactivate your own account.", "danger")
        return redirect(url_for("admin.users"))
    user = db.fetch_one("SELECT * FROM users WHERE id=%s", (user_id,))
    if not user:
        abort(404)
    new_state = 0 if user["is_active"] else 1
    db.execute("UPDATE users SET is_active=%s WHERE id=%s", (new_state, user_id))
    audit.record("admin.user_status", entity="user", entity_id=user_id, details={"active": bool(new_state)})
    flash(f"{user['name']} {'reactivated' if new_state else 'deactivated'}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/role", methods=["POST"])
@login_required
@admin_required
def change_role(user_id: int):
    if user_id == current_user.id:
        flash("You cannot change your own role.", "danger")
        return redirect(url_for("admin.users"))
    user = db.fetch_one("SELECT * FROM users WHERE id=%s", (user_id,))
    if not user:
        abort(404)
    new_role = "user" if user["role"] == "admin" else "admin"
    db.execute("UPDATE users SET role=%s WHERE id=%s", (new_role, user_id))
    audit.record("admin.user_role", entity="user", entity_id=user_id, details={"role": new_role})
    flash(f"{user['name']} is now an {new_role}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/password", methods=["POST"])
@login_required
@admin_required
def reset_password(user_id: int):
    user = db.fetch_one("SELECT * FROM users WHERE id=%s", (user_id,))
    if not user:
        abort(404)
    password = request.form.get("password") or ""
    if len(password) < 8 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        flash("Password must be at least 8 characters and contain a letter and a number.", "danger")
        return redirect(url_for("admin.users"))
    db.execute(
        "UPDATE users SET password_hash=%s WHERE id=%s",
        (generate_password_hash(password, method="pbkdf2:sha256:600000"), user_id),
    )
    audit.record("admin.password_reset", entity="user", entity_id=user_id)
    flash(f"Password reset for {user['name']}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
    if request.method == "POST":
        try:
            daily = parse_float(request.form.get("daily_limit_l"), "Daily limit", minimum=10, maximum=100000)
            monthly = parse_float(request.form.get("monthly_limit_l"), "Monthly limit", minimum=100, maximum=3000000)
            target = parse_float(request.form.get("conservation_target_lpd"), "Conservation target",
                                 minimum=10, maximum=1000)
            tariff = parse_float(request.form.get("tariff_per_kl"), "Tariff", minimum=0, maximum=10000)
            threshold = int(request.form.get("alert_threshold_pct") or 80)
            if threshold < 10 or threshold > 100:
                raise ValidationError("Alert threshold must be between 10 and 100.")
            if monthly < daily:
                raise ValidationError("Monthly limit must be greater than or equal to the daily limit.")
            before = get_settings()
            update_settings(
                {
                    "daily_limit_l": daily,
                    "monthly_limit_l": monthly,
                    "alert_threshold_pct": threshold,
                    "conservation_target_lpd": target,
                    "tariff_per_kl": tariff,
                    "auto_anchor": "1" if request.form.get("auto_anchor") else "0",
                },
                current_user.id,
            )
            audit.record(
                "admin.settings_update",
                entity="settings",
                details={
                    "before": {k: str(before.get(k)) for k in ("daily_limit_l", "monthly_limit_l", "alert_threshold_pct")},
                    "after": {"daily": daily, "monthly": monthly, "threshold": threshold},
                },
            )
            flash("Global water limits and settings saved. Household overrides still take precedence.", "success")
        except (ValidationError, ValueError) as exc:
            flash(str(exc), "danger")
        return redirect(url_for("admin.settings"))

    overrides = db.fetch_all(
        """SELECT hl.*, h.household_code, h.name FROM household_limits hl
           JOIN households h ON h.id=hl.household_id ORDER BY h.household_code"""
    )
    households = db.fetch_all(
        """SELECT h.id, h.household_code, h.name, h.members_count,
                  (SELECT COUNT(*) FROM readings r WHERE r.household_id=h.id) AS readings
           FROM households h ORDER BY h.household_code"""
    )
    return render_template(
        "admin/settings.html",
        settings=get_settings(),
        overrides=overrides,
        households=households,
        page_title="Water limits & settings",
    )


@admin_bp.route("/audit-log")
@login_required
@admin_required
def audit_log():
    action = (request.args.get("action") or "").strip()
    where, params = "1=1", []
    if action:
        where = "a.action LIKE %s"
        params.append(action + "%")
    rows = db.fetch_all(
        f"""SELECT a.*, u.name AS user_name FROM audit_log a LEFT JOIN users u ON u.id=a.user_id
            WHERE {where} ORDER BY a.id DESC LIMIT 200""",
        params,
    )
    actions = db.fetch_all("SELECT DISTINCT action FROM audit_log ORDER BY action")
    return render_template(
        "admin/audit_log.html", logs=rows, actions=actions, selected_action=action,
        page_title="Audit log",
    )


@admin_bp.route("/integrity-audit", methods=["POST"])
@login_required
@admin_required
def integrity_audit():
    result = audit_readings(current_user, verify_chain=False, persist=True, limit=500)
    audit.record("admin.integrity_audit", entity="system", details=result["summary"])
    summary = result["summary"]
    if summary["tampered"]:
        flash(
            f"Integrity audit complete: {summary['tampered']} tampered record(s) detected and flagged. "
            f"{summary['valid']} records verified.",
            "danger",
        )
    else:
        flash(
            f"Integrity audit complete: all {summary['total']} records match their stored digests.",
            "success",
        )
    return redirect(url_for("blockchain.audit_page"))


@admin_bp.route("/tamper-drill/<int:reading_id>", methods=["POST"])
@login_required
@admin_required
def tamper_drill(reading_id: int):
    reading = db.fetch_one(
        """SELECT r.*, h.chain_ref, h.meter_id, h.household_code FROM readings r
           JOIN households h ON h.id=r.household_id WHERE r.id=%s""",
        (reading_id,),
    )
    if not reading:
        abort(404)
    original = reading["consumption_l"]
    tampered = round(float(original) + 75, 2)
    detected = False
    recomputed = None
    try:
        db.execute("UPDATE readings SET consumption_l=%s WHERE id=%s", (tampered, reading_id))
        modified = db.fetch_one(
            """SELECT r.*, h.chain_ref, h.meter_id FROM readings r JOIN households h ON h.id=r.household_id
               WHERE r.id=%s""",
            (reading_id,),
        )
        recomputed = compute_record_hash(modified)
        detected = recomputed.lower() != modified["record_hash"].lower()
        audit.record(
            "admin.tamper_drill",
            entity="reading",
            entity_id=reading_id,
            details={"original_l": float(original), "tampered_l": tampered, "detected": detected},
        )
    finally:
        db.execute("UPDATE readings SET consumption_l=%s WHERE id=%s", (original, reading_id))

    restored = db.fetch_one(
        """SELECT r.*, h.chain_ref, h.meter_id FROM readings r JOIN households h ON h.id=r.household_id
           WHERE r.id=%s""",
        (reading_id,),
    )
    restored_ok = compute_record_hash(restored).lower() == restored["record_hash"].lower()
    if detected:
        flash(
            f"Tamper drill on reading #{reading_id}: consumption was changed from {float(original):,.0f} L to "
            f"{tampered:,.0f} L without updating the digest. The recomputed keccak256 digest did not match, so "
            f"tampering was detected. Original value restored ({'digest valid' if restored_ok else 'restore failed'}).",
            "warning",
        )
    else:
        flash("Tamper drill did not detect the change. Check the hashing configuration.", "danger")
    return redirect(url_for("blockchain.audit_page"))
