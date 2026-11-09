"""Reading list, manual entry, detail and re-anchoring."""
from __future__ import annotations

from datetime import date, datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import db
from ..services import audit
from ..services.analytics import accessible_household_ids, get_household_or_none
from ..services.chain import ChainError, chain
from ..services.hashing import compute_record_hash
from ..services.readings import create_reading, latest_reading
from ..services.settings import get_settings, resolve_limits
from ..utils import ValidationError

readings_bp = Blueprint("readings", __name__, url_prefix="/readings")

PAGE_SIZE = 25


@readings_bp.route("/")
@login_required
def index():
    household_ids = accessible_household_ids(current_user)
    if not household_ids:
        return render_template(
            "readings.html", readings=[], households=[], filters={}, pagination={}, page_title="Meter readings"
        )

    filters = {
        "household_id": request.args.get("household_id", ""),
        "status": request.args.get("status", ""),
        "from": request.args.get("from", ""),
        "to": request.args.get("to", ""),
    }
    where = [f"r.household_id IN ({','.join(['%s'] * len(household_ids))})"]
    params: list = list(household_ids)
    if filters["household_id"] and filters["household_id"].isdigit():
        hid = int(filters["household_id"])
        if hid not in household_ids:
            abort(404)
        where.append("r.household_id=%s")
        params.append(hid)
    if filters["status"] in ("pending", "anchored", "failed", "tampered"):
        where.append("r.status=%s")
        params.append(filters["status"])
    if filters["from"]:
        try:
            where.append("r.reading_date >= %s")
            params.append(date.fromisoformat(filters["from"]))
        except ValueError:
            filters["from"] = ""
    if filters["to"]:
        try:
            where.append("r.reading_date <= %s")
            params.append(date.fromisoformat(filters["to"]))
        except ValueError:
            filters["to"] = ""

    clause = " AND ".join(where)
    total = db.fetch_value(
        f"SELECT COUNT(*) FROM readings r WHERE {clause}", params, default=0
    ) or 0
    page = max(int(request.args.get("page", 1) or 1), 1)
    pages = max((int(total) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(page, pages)
    rows = db.fetch_all(
        f"""SELECT r.*, h.household_code, h.name AS household_name, u.name AS recorder_name
            FROM readings r JOIN households h ON h.id=r.household_id
            LEFT JOIN users u ON u.id=r.recorded_by
            WHERE {clause} ORDER BY r.reading_date DESC, r.id DESC
            LIMIT %s OFFSET %s""",
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
    )
    households = db.fetch_all(
        f"""SELECT id, household_code, name FROM households WHERE id IN ({','.join(['%s'] * len(household_ids))})
            ORDER BY household_code""",
        household_ids,
    )
    pagination = {"page": page, "pages": pages, "total": int(total), "page_size": PAGE_SIZE}
    return render_template(
        "readings.html",
        readings=rows,
        households=households,
        filters=filters,
        pagination=pagination,
        page_title="Meter readings",
    )


@readings_bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    household_ids = accessible_household_ids(current_user)
    households = db.fetch_all(
        f"""SELECT h.*, u.name AS owner_name FROM households h LEFT JOIN users u ON u.id=h.user_id
            WHERE h.id IN ({','.join(['%s'] * len(household_ids))}) AND h.status='active'
            ORDER BY h.household_code"""
        if household_ids
        else "SELECT NULL AS id LIMIT 0",
        household_ids if household_ids else [],
    )
    if not households:
        flash("Create a household before recording meter readings.", "warning")
        return redirect(url_for("households.index"))

    meta = {}
    for household in households:
        latest = latest_reading(household["id"])
        meta[household["id"]] = {
            "latest_current": float(latest["current_reading_m3"]) if latest else None,
            "latest_date": latest["reading_date"].isoformat() if latest else None,
            "members": household["members_count"],
            "code": household["household_code"],
            "name": household["name"],
        }

    if request.method == "POST":
        household_id = request.form.get("household_id") or ""
        if not household_id.isdigit() or int(household_id) not in household_ids:
            flash("Select a valid household.", "danger")
            return render_template("reading_new.html", households=households, meta=meta,
                                   selected=request.form.get("household_id"), page_title="Record reading"), 400
        household = get_household_or_none(int(household_id), current_user)
        if not household:
            abort(404)
        try:
            result = create_reading(
                household,
                reading_date=request.form.get("reading_date"),
                current_reading_m3=request.form.get("current_reading_m3"),
                previous_reading_m3=request.form.get("previous_reading_m3"),
                notes=request.form.get("notes"),
                source=request.form.get("reading_source") or "manual",
                recorded_by=current_user.id,
            )
            reading = result["reading"]
            audit.record(
                "reading.create",
                entity="reading",
                entity_id=reading["id"],
                details={
                    "household": reading["household_code"],
                    "date": str(reading["reading_date"]),
                    "consumption_l": float(reading["consumption_l"]),
                    "record_hash": reading["record_hash"],
                },
            )
            for alert in result["alerts"]:
                audit.record(
                    "alert.raise",
                    entity="alert",
                    entity_id=alert["id"],
                    details={"type": alert["type"], "severity": alert["severity"], "reading_id": reading["id"]},
                )
                flash(f"{alert['title']}: {alert['message']}", "warning" if alert["severity"] == "warning" else "danger")

            auto_anchor = str(get_settings().get("auto_anchor", "1")) == "1"
            if auto_anchor and chain.is_configured:
                try:
                    chain.submit_anchor(reading["id"])
                    flash("Reading saved. Its proof is being anchored on the configured chain.", "info")
                except ChainError as exc:
                    flash(f"Reading saved, but anchoring could not start: {exc}", "warning")
            else:
                flash("Reading saved. Anchor its proof from the record page when you're ready.", "info")
            return redirect(url_for("readings.detail", reading_id=reading["id"]))
        except ValidationError as exc:
            flash(str(exc), "danger")
            return render_template(
                "reading_new.html",
                households=households,
                meta=meta,
                selected=household_id,
                form=request.form,
                page_title="Record reading",
            ), 400

    selected = request.args.get("household_id") or str(households[0]["id"])
    return render_template(
        "reading_new.html",
        households=households,
        meta=meta,
        selected=selected,
        form={},
        page_title="Record reading",
    )


@readings_bp.route("/<int:reading_id>")
@login_required
def detail(reading_id: int):
    reading = db.fetch_one(
        """SELECT r.*, h.chain_ref, h.meter_id, h.household_code, h.name AS household_name,
                  h.members_count, h.city, u.name AS recorder_name
           FROM readings r JOIN households h ON h.id=r.household_id
           LEFT JOIN users u ON u.id=r.recorded_by WHERE r.id=%s""",
        (reading_id,),
    )
    if not reading:
        abort(404)
    household = get_household_or_none(reading["household_id"], current_user)
    if not household:
        abort(404)
    recomputed = compute_record_hash(reading)
    db_match = recomputed.lower() == reading["record_hash"].lower()
    limits = resolve_limits(reading["household_id"])
    transactions = db.fetch_all(
        "SELECT * FROM blockchain_transactions WHERE reading_id=%s ORDER BY id DESC", (reading_id,)
    )
    alerts = db.fetch_all("SELECT * FROM alerts WHERE reading_id=%s ORDER BY created_at DESC", (reading_id,))
    meta = {
        "household_ref": reading["chain_ref"],
        "sequence": reading["sequence"],
        "consumption_ml": int(round(float(reading["consumption_l"]) * 1000)),
        "reading_date_unix": int(
            datetime(
                reading["reading_date"].year,
                reading["reading_date"].month,
                reading["reading_date"].day,
                tzinfo=timezone.utc,
            ).timestamp()
        ),
        "record_hash": reading["record_hash"],
        "recomputed_hash": recomputed,
        "db_match": db_match,
    }
    return render_template(
        "reading_detail.html",
        reading=reading,
        household=household,
        limits=limits,
        transactions=transactions,
        alerts=alerts,
        meta=meta,
        db_match=db_match,
        page_title=f"Reading #{reading['id']}",
    )


@readings_bp.route("/<int:reading_id>/reanchor", methods=["POST"])
@login_required
def reanchor(reading_id: int):
    reading = db.fetch_one(
        """SELECT r.*, h.chain_ref, h.meter_id FROM readings r
           JOIN households h ON h.id=r.household_id WHERE r.id=%s""",
        (reading_id,),
    )
    if not reading:
        abort(404)
    household = get_household_or_none(reading["household_id"], current_user)
    if not household:
        abort(404)
    if reading["status"] == "anchored":
        flash("This record is already anchored.", "info")
        return redirect(url_for("readings.detail", reading_id=reading_id))
    recomputed = compute_record_hash(reading)
    if recomputed.lower() != reading["record_hash"].lower():
        flash("Integrity check failed. This record has been modified and cannot be anchored.", "danger")
        db.execute("UPDATE readings SET status='tampered' WHERE id=%s", (reading_id,))
        return redirect(url_for("readings.detail", reading_id=reading_id))
    try:
        chain.submit_anchor(reading_id)
        audit.record("reading.reanchor", entity="reading", entity_id=reading_id, details={"mode": chain.mode})
        flash(f"Anchoring started on {chain.mode}. Refresh in a few moments to see the transaction.", "info")
    except ChainError as exc:
        flash(f"Anchoring failed: {exc}", "danger")
    return redirect(url_for("readings.detail", reading_id=reading_id))
