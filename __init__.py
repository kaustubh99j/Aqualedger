"""AquaLedger Flask application factory."""
from __future__ import annotations

import logging
from datetime import date

from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user

from config import Config
from . import db
from .models import User
from .security import init_security
from .services.chain import chain
from .utils import fmt_l, fmt_money, humanize_dt, json_default, pct, short_hash, time_ago

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to continue."
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Authentication required"}), 401
    return redirect(url_for("auth.login", next=request.path))


def create_app(config_object=None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(config_object or Config)
    app.json.default = json_default

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db.init_pool(app)
    init_security(app)
    login_manager.init_app(app)
    chain.init_app(app)

    # Blueprints -----------------------------------------------------------------
    from .routes.main import main_bp
    from .routes.auth import auth_bp
    from .routes.dashboard import dashboard_bp
    from .routes.households import households_bp
    from .routes.readings import readings_bp
    from .routes.blockchain import blockchain_bp
    from .routes.alerts import alerts_bp
    from .routes.reports import reports_bp
    from .routes.admin import admin_bp
    from .routes.api import api_bp
    from .routes.wallet import wallet_bp

    for blueprint in (
        main_bp,
        auth_bp,
        dashboard_bp,
        households_bp,
        readings_bp,
        blockchain_bp,
        alerts_bp,
        reports_bp,
        admin_bp,
        wallet_bp,
        api_bp,
    ):
        app.register_blueprint(blueprint)

    # Template helpers -----------------------------------------------------------
    app.jinja_env.filters["litres"] = fmt_l
    app.jinja_env.filters["money"] = fmt_money
    app.jinja_env.filters["hash"] = short_hash
    app.jinja_env.filters["dt"] = humanize_dt
    app.jinja_env.filters["ago"] = time_ago
    app.jinja_env.filters["pct"] = pct

    @app.context_processor
    def inject_globals():
        counts = {"unread_alerts": 0, "households": 0}
        if current_user.is_authenticated:
            try:
                if current_user.is_admin:
                    counts["unread_alerts"] = int(
                        db.fetch_value("SELECT COUNT(*) FROM alerts WHERE is_read=0", default=0) or 0
                    )
                    counts["households"] = int(
                        db.fetch_value("SELECT COUNT(*) FROM households WHERE status='active'", default=0) or 0
                    )
                else:
                    counts["unread_alerts"] = int(
                        db.fetch_value(
                            """SELECT COUNT(*) FROM alerts a JOIN households h ON h.id=a.household_id
                               WHERE h.user_id=%s AND a.is_read=0""",
                            (current_user.id,),
                            default=0,
                        )
                        or 0
                    )
                    counts["households"] = int(
                        db.fetch_value(
                            "SELECT COUNT(*) FROM households WHERE user_id=%s AND status='active'",
                            (current_user.id,),
                            default=0,
                        )
                        or 0
                    )
            except Exception:
                pass
        return {
            "app_name": "AquaLedger",
            "current_year": date.today().year,
            "chain_mode": app.config["BLOCKCHAIN_MODE"],
            "nav_counts": counts,
            "current_path": request.path,
        }

    @app.context_processor
    def inject_json_default():
        return {"json_default": json_default}

    # Error handlers -------------------------------------------------------------
    def wants_json() -> bool:
        return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"

    @app.errorhandler(400)
    def bad_request(exc):
        if wants_json():
            return jsonify({"ok": False, "error": getattr(exc, "description", "Bad request")}), 400
        return render_template("errors/error.html", code=400, title="Bad request",
                               message=getattr(exc, "description", "The request could not be processed.")), 400

    @app.errorhandler(403)
    def forbidden(exc):
        if wants_json():
            return jsonify({"ok": False, "error": "You do not have permission to perform this action."}), 403
        return render_template("errors/error.html", code=403, title="Access denied",
                               message="You do not have permission to view this page."), 403

    @app.errorhandler(404)
    def not_found(exc):
        if wants_json():
            return jsonify({"ok": False, "error": "Resource not found"}), 404
        return render_template("errors/error.html", code=404, title="Page not found",
                               message="The page or record you requested does not exist."), 404

    @app.errorhandler(500)
    def server_error(exc):
        app.logger.exception("Unhandled server error")
        if wants_json():
            return jsonify({"ok": False, "error": "Internal server error"}), 500
        return render_template("errors/error.html", code=500, title="Something went wrong",
                               message="An unexpected error occurred. Please try again."), 500

    @app.route("/health")
    def health():
        db_ok = False
        try:
            db_ok = bool(db.fetch_value("SELECT 1", default=0))
        except Exception:
            pass
        status = chain.status()
        return jsonify(
            {
                "status": "ok" if db_ok else "degraded",
                "database": db_ok,
                "database_error": db.pool_error(),
                "blockchain": {
                    "mode": status.get("mode"),
                    "connected": status.get("connected"),
                    "error": status.get("error"),
                },
            }
        )

    return app
