"""Blockchain explorer and integrity audit pages."""
from __future__ import annotations

from datetime import date, datetime

from flask import Blueprint, abort, current_app, render_template, request
from flask_login import current_user, login_required

from .. import db
from ..services.analytics import accessible_household_ids, audit_readings
from ..services.chain import chain
from ..utils import json_default

blockchain_bp = Blueprint("blockchain", __name__, url_prefix="/blockchain")


@blockchain_bp.route("/")
@login_required
def index():
    ids = accessible_household_ids(current_user)
    status = chain.status()
    transactions = []
    summary = {"anchored": 0, "pending": 0, "failed": 0, "tampered": 0, "total": 0}
    if ids:
        placeholders = ",".join(["%s"] * len(ids))
        transactions = db.fetch_all(
            f"""SELECT t.*, r.reading_date, r.consumption_l, r.record_hash AS reading_hash,
                       h.household_code, h.name AS household_name
                FROM blockchain_transactions t
                JOIN readings r ON r.id=t.reading_id
                JOIN households h ON h.id=t.household_id
                WHERE t.household_id IN ({placeholders})
                ORDER BY t.id DESC LIMIT 25""",
            ids,
        )
        row = db.fetch_one(
            f"""SELECT COUNT(*) AS total, SUM(status='anchored') AS anchored,
                       SUM(status='pending') AS pending, SUM(status='failed') AS failed,
                       SUM(status='tampered') AS tampered
                FROM readings WHERE household_id IN ({placeholders})""",
            ids,
        ) or {}
        summary = {
            "total": int(row.get("total") or 0),
            "anchored": int(row.get("anchored") or 0),
            "pending": int(row.get("pending") or 0),
            "failed": int(row.get("failed") or 0),
            "tampered": int(row.get("tampered") or 0),
        }
    audit = audit_readings(current_user, verify_chain=False, limit=100)
    explorer = current_app.config["EXPLORER_BASE_URL"].rstrip("/")
    return render_template(
        "blockchain.html",
        status=status,
        transactions=transactions,
        summary=summary,
        audit=audit,
        explorer=explorer,
        page_title="Blockchain proofs",
    )


@blockchain_bp.route("/audit")
@login_required
def audit_page():
    verify = request.args.get("onchain") == "1" and not chain.is_simulation
    limit = 25 if verify else 100
    audit = audit_readings(current_user, verify_chain=verify, limit=limit)
    simulation_chain = chain.verify_simulation_chain() if chain.is_simulation else None
    return render_template(
        "audit.html",
        audit=audit,
        simulation_chain=simulation_chain,
        verified_on_chain=verify,
        mode=chain.mode,
        page_title="Integrity audit",
    )
