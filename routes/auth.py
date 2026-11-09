"""Registration, login and logout."""
from __future__ import annotations

import secrets

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user

from .. import db
from ..models import User
from ..security import clear_failures, client_key, login_allowed, register_failure
from ..services import audit
from ..services.hashing import utc_now
from ..utils import EMAIL_RE

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        phone = (request.form.get("phone") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        errors = []
        if len(name) < 2 or len(name) > 120:
            errors.append("Full name must be between 2 and 120 characters.")
        if not EMAIL_RE.match(email) or len(email) > 190:
            errors.append("Enter a valid email address.")
        if phone and (not phone.replace("+", "").replace("-", "").replace(" ", "").isdigit() or len(phone) > 30):
            errors.append("Phone number format is invalid.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters long.")
        elif not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
            errors.append("Password must contain at least one letter and one number.")
        if password != confirm:
            errors.append("Passwords do not match.")
        if db.fetch_one("SELECT id FROM users WHERE email=%s", (email,)):
            errors.append("An account with this email already exists.")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("auth/register.html", form=request.form), 400

        from werkzeug.security import generate_password_hash

        user_id = db.execute(
            """INSERT INTO users (name, email, password_hash, role, phone, created_at)
               VALUES (%s,%s,%s,'user',%s,%s)""",
            (name, email, generate_password_hash(password, method="pbkdf2:sha256:600000"), phone or None, utc_now()),
        )
        audit.record("user.register", entity="user", entity_id=user_id, details={"email": email}, user_id=user_id)
        flash("Account created successfully. Sign in to start monitoring your water usage.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html", form={})


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        remember = bool(request.form.get("remember"))
        ip_key = client_key("login-ip")
        email_key = client_key(f"login-email:{email}")
        max_attempts = int(current_app.config.get("LOGIN_MAX_ATTEMPTS", 6))
        window = int(current_app.config.get("LOGIN_WINDOW_SECONDS", 300))

        if not login_allowed(ip_key, max_attempts, window) or not login_allowed(email_key, max_attempts, window):
            flash("Too many failed sign-in attempts. Please wait a few minutes and try again.", "danger")
            return render_template("auth/login.html", form=request.form), 429

        user = User.by_email(email)
        if not user or not user.is_active or not _check_password(user, password):
            register_failure(ip_key)
            register_failure(email_key)
            audit.record("auth.login_failed", entity="user", entity_id=user.id if user else None,
                         details={"email": email})
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html", form=request.form), 401

        clear_failures(ip_key)
        clear_failures(email_key)
        # Session fixation protection: discard the anonymous session first.
        session.clear()
        login_user(user, remember=remember)
        session["_csrf_token"] = secrets.token_urlsafe(32)
        db.execute("UPDATE users SET last_login=%s WHERE id=%s", (utc_now(), user.id))
        audit.record("auth.login", entity="user", entity_id=user.id, user_id=user.id)
        next_url = request.args.get("next") or request.form.get("next")
        if next_url and next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect(url_for("dashboard.index"))

    return render_template("auth/login.html", form={})


def _check_password(user: User, password: str) -> bool:
    from werkzeug.security import check_password_hash

    return check_password_hash(user._row["password_hash"], password)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    if current_user.is_authenticated:
        audit.record("auth.logout", entity="user", entity_id=current_user.id)
    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("main.landing"))
