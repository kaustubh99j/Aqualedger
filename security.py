"""CSRF protection, login throttling and access decorators."""
from __future__ import annotations

import hmac
import secrets
import threading
import time
from functools import wraps

from flask import abort, jsonify, redirect, request, session, url_for
from flask_login import current_user

_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()


def get_csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def csrf_protect():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    token = request.headers.get("X-CSRF-Token")
    if not token:
        token = request.form.get("csrf_token")
    if not token:
        payload = request.get_json(silent=True) or {}
        token = payload.get("csrf_token")
    expected = session.get("_csrf_token", "")
    if not token or not expected or not hmac.compare_digest(str(token), str(expected)):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Invalid or missing CSRF token. Refresh the page and retry."}), 403
        abort(400, description="Invalid or missing CSRF token. Refresh the page and retry.")
    return None


def init_security(app):
    app.before_request(csrf_protect)

    @app.context_processor
    def inject_csrf():
        return {"csrf_token": get_csrf_token}


def client_key(prefix: str) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.remote_addr or "unknown"
    return f"{prefix}:{ip}"


def login_allowed(key: str, max_attempts: int, window: int) -> bool:
    now = time.time()
    with _attempts_lock:
        entries = [ts for ts in _attempts.get(key, []) if now - ts < window]
        _attempts[key] = entries
        return len(entries) < max_attempts


def register_failure(key: str) -> int:
    with _attempts_lock:
        _attempts.setdefault(key, []).append(time.time())
        return len(_attempts[key])


def clear_failures(key: str) -> None:
    with _attempts_lock:
        _attempts.pop(key, None)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Authentication required"}), 401
            return redirect(url_for("auth.login", next=request.path))
        if not current_user.is_admin:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Administrator access required"}), 403
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def api_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"ok": False, "error": "Authentication required"}), 401
        return view(*args, **kwargs)

    return wrapped
