"""JSON APIs used by the dashboards, charts and blockchain explorer."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, current_app, request
from flask_login import current_user, login_required

from .. import db
from ..security import api_login_required
from ..services import audit
from ..services.analytics import (
    accessible_household_ids,
    audit_readings,
    conservation_score,
    daily_series,
    dashboard_stats,
    get_household_or_none,
    monthly_report,
    monthly_series,
)
from ..services.alerts import create_alert
from ..services.chain import ChainError, chain
from ..services.hashing import compute_record_hash
from ..services.settings import get_settings, resolve_limits
from ..utils import api_error, api_ok, json_default

api_bp = Blueprint("api", __name__, url_prefix="/api")


# ------------------------------------------------------------------ helpers
def _scope_households() -> list[int] | None:
    ids = accessible_household_ids(current_user)
    raw = request.args.get("household_id")
    if raw in (None, "", "all"):
        return ids
    if not raw.isdigit():
        return None
    hid = int(raw)
    if hid not in ids:
        return None
    return [hid]


def _explorer(tx_hash: str | None, mode: str | None) -> str | None:
    if not tx_hash or mode != "sepolia":
        return None
    base = current_app.config["EXPLORER_BASE_URL"].rstrip("/")
    return f"{base}/tx/{tx_hash}"


def serialize_reading(row: dict, *, include_household: bool = True) -> dict:
    data = {
        "id": row["id"],
        "reading_date": row["reading_date"].isoformat() if isinstance(row["reading_date"], date) else row["reading_date"],
        "sequence": row.get("sequence"),
        "previous_reading_m3": float(row["previous_reading_m3"]) if row.get("previous_reading_m3") is not None else None,
        "current_reading_m3": float(row["current_reading_m3"]) if row.get("current_reading_m3") is not None else None,
        "consumption_l": float(row["consumption_l"]) if row.get("consumption_l") is not None else None,
        "status": row.get("status"),
        "chain_mode": row.get("chain_mode"),
        "chain_tx_hash": row.get("chain_tx_hash"),
        "chain_block": row.get("chain_block"),
        "chain_timestamp": json_default(row["chain_timestamp"]) if row.get("chain_timestamp") else None,
        "record_hash": row.get("record_hash"),
        "notes": row.get("notes"),
        "explorer_url": _explorer(row.get("chain_tx_hash"), row.get("chain_mode")),
        "created_at": json_default(row["created_at"]) if row.get("created_at") else None,
    }
    if include_household:
        data["household_id"] = row.get("household_id")
        data["household_code"] = row.get("household_code")
        data["household_name"] = row.get("household_name")
    return data


# ------------------------------------------------------------------ summary
@api_bp.get("/dashboard/summary")
@api_login_required
def dashboard_summary():
    ids = _scope_households()
    if ids is None:
        return api_error("Household not found or not accessible", 404)
    if not ids:
        return api_ok(summary={}, empty=True)
    # dashboard_stats expects a user object; scope filtering is applied client-side
    if request.args.get("household_id") not in (None, "", "all"):
        from ..services.analytics import consumption_between, limit_total, average_daily

        hid = ids[0]
        limits = resolve_limits(hid)
        today = date.today()
        today_l = consumption_between(ids, today, today)
        week_l = consumption_between(ids, date.fromordinal(today.toordinal() - 6), today)
        month_l = consumption_between(ids, today.replace(day=1), today)
        return api_ok(
            summary={
                "today_l": round(today_l, 2),
                "week_l": round(week_l, 2),
                "month_l": round(month_l, 2),
                "avg_daily_l": average_daily(ids),
                "daily_limit_l": limits["daily_limit_l"],
                "monthly_limit_l": limits["monthly_limit_l"],
                "household_count": 1,
            }
        )
    stats = dashboard_stats(current_user)
    stats.pop("last_reading", None)
    stats.pop("month_start", None)
    stats.pop("month_end", None)
    return api_ok(summary=stats)


@api_bp.get("/charts/daily")
@api_login_required
def chart_daily():
    ids = _scope_households()
    if ids is None:
        return api_error("Household not found or not accessible", 404)
    try:
        days = max(7, min(int(request.args.get("days", 30)), 90))
    except ValueError:
        days = 30
    series = daily_series(ids, days)
    limit = 0.0
    for hid in ids:
        limit += resolve_limits(hid)["daily_limit_l"]
    return api_ok(
        labels=[day.strftime("%d %b") for day in (series["start"] + timedelta(days=i) for i in range(days))],
        iso_labels=series["labels"],
        values=series["values"],
        entries=series["entries"],
        daily_limit_l=round(limit, 2),
        total_l=round(sum(series["values"]), 2),
        days=days,
    )


@api_bp.get("/charts/monthly")
@api_login_required
def chart_monthly():
    ids = _scope_households()
    if ids is None:
        return api_error("Household not found or not accessible", 404)
    try:
        months = max(6, min(int(request.args.get("months", 12)), 24))
    except ValueError:
        months = 12
    series = monthly_series(ids, months)
    monthly_limit = 0.0
    for hid in ids:
        monthly_limit += resolve_limits(hid)["monthly_limit_l"]
    return api_ok(
        labels=series["labels"],
        values=series["values"],
        monthly_limit_l=round(monthly_limit, 2),
        total_l=round(sum(series["values"]), 2),
        months=months,
    )


@api_bp.get("/households")
@api_login_required
def households_list():
    ids = accessible_household_ids(current_user)
    if not ids:
        return api_ok(households=[])
    rows = db.fetch_all(
        f"""SELECT h.id, h.household_code, h.name, h.members_count, h.status,
                   (SELECT COUNT(*) FROM readings r WHERE r.household_id=h.id) AS readings
            FROM households h WHERE h.id IN ({','.join(['%s'] * len(ids))}) ORDER BY h.household_code""",
        ids,
    )
    return api_ok(households=rows)


@api_bp.get("/households/<int:household_id>/latest")
@api_login_required
def household_latest(household_id: int):
    household = get_household_or_none(household_id, current_user)
    if not household:
        return api_error("Household not found", 404)
    latest = db.fetch_one(
        "SELECT * FROM readings WHERE household_id=%s ORDER BY reading_date DESC, id DESC LIMIT 1",
        (household_id,),
    )
    limits = resolve_limits(household_id)
    return api_ok(
        household={"id": household["id"], "code": household["household_code"], "name": household["name"],
                   "members": household["members_count"]},
        latest=serialize_reading(latest, include_household=False) if latest else None,
        limits=limits,
    )


@api_bp.get("/score/<int:household_id>")
@api_login_required
def score(household_id: int):
    household = get_household_or_none(household_id, current_user)
    if not household:
        return api_error("Household not found", 404)
    return api_ok(score=conservation_score(household))


# ------------------------------------------------------------------ readings
@api_bp.get("/readings")
@api_login_required
def readings_list():
    ids = _scope_households()
    if ids is None:
        return api_error("Household not found", 404)
    if not ids:
        return api_ok(readings=[])
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except ValueError:
        limit = 50
    rows = db.fetch_all(
        f"""SELECT r.*, h.household_code, h.name AS household_name
            FROM readings r JOIN households h ON h.id=r.household_id
            WHERE r.household_id IN ({','.join(['%s'] * len(ids))})
            ORDER BY r.reading_date DESC, r.id DESC LIMIT %s""",
        ids + [limit],
    )
    return api_ok(readings=[serialize_reading(row) for row in rows])


@api_bp.get("/readings/<int:reading_id>/verify")
@api_login_required
def verify_reading(reading_id: int):
    reading = db.fetch_one(
        """SELECT r.*, h.chain_ref, h.meter_id, h.household_code, h.name AS household_name
           FROM readings r JOIN households h ON h.id=r.household_id WHERE r.id=%s""",
        (reading_id,),
    )
    if not reading:
        return api_error("Reading not found", 404)
    household = get_household_or_none(reading["household_id"], current_user)
    if not household:
        return api_error("Reading not found", 404)

    recomputed = compute_record_hash(reading)
    db_match = recomputed.lower() == reading["record_hash"].lower()
    chain_result = None
    if reading["status"] == "anchored":
        try:
            chain_result = chain.verify_record(reading, household, recomputed)
        except ChainError as exc:
            chain_result = {"error": str(exc), "hash_matches": False, "exists": False}

    verdict = "valid"
    message = "Record matches its stored digest and anchored proof."
    if not db_match:
        verdict = "tampered"
        message = "The database values no longer match the stored record digest. This record was modified after it was created."
        db.execute("UPDATE readings SET status='tampered' WHERE id=%s", (reading_id,))
        create_alert(
            reading["household_id"], "tamper", "critical", "Record integrity failure",
            f"Reading #{reading_id} for {reading['household_code']} failed integrity verification. "
            "Database values no longer match the stored digest.",
            reading_id=reading_id,
        )
        audit.record("integrity.tamper_detected", entity="reading", entity_id=reading_id,
                     details={"stored": reading["record_hash"], "recomputed": recomputed})
    elif reading["status"] in ("pending", "failed"):
        verdict = "pending"
        message = "The off-chain record is intact but its proof is not anchored yet."
    elif chain_result and chain_result.get("error"):
        verdict = "error"
        message = f"Off-chain digest is valid, but the chain could not be checked: {chain_result['error']}"
    elif chain_result and not chain_result.get("hash_matches"):
        verdict = "tampered"
        message = "The on-chain record hash does not match the off-chain record. The proof was altered or the record was changed."
        db.execute("UPDATE readings SET status='tampered' WHERE id=%s", (reading_id,))
        create_alert(
            reading["household_id"], "tamper", "critical", "On-chain proof mismatch",
            f"Reading #{reading_id} for {reading['household_code']} exists on-chain but its digest differs from the current record.",
            reading_id=reading_id,
        )
    elif chain_result and not chain_result.get("consumption_matches", True):
        verdict = "tampered"
        message = "The anchored consumption does not match the off-chain consumption value."
        db.execute("UPDATE readings SET status='tampered' WHERE id=%s", (reading_id,))

    audit.record("integrity.verify", entity="reading", entity_id=reading_id, details={"verdict": verdict})
    return api_ok(
        reading_id=reading_id,
        verdict=verdict,
        message=message,
        db={
            "stored_hash": reading["record_hash"],
            "recomputed_hash": recomputed,
            "match": db_match,
        },
        chain=chain_result,
        status=reading["status"],
        tx_hash=reading["chain_tx_hash"],
        explorer_url=_explorer(reading["chain_tx_hash"], reading["chain_mode"]),
    )


@api_bp.get("/readings/<int:reading_id>/anchor-status")
@api_login_required
def anchor_status(reading_id: int):
    row = db.fetch_one(
        """SELECT r.id, r.status, r.chain_mode, r.chain_tx_hash, r.chain_block, r.chain_timestamp,
                  r.chain_confirmations, r.anchored_by
           FROM readings r JOIN households h ON h.id=r.household_id WHERE r.id=%s""",
        (reading_id,),
    )
    if not row:
        return api_error("Reading not found", 404)
    if not get_household_or_none(
        db.fetch_value("SELECT household_id FROM readings WHERE id=%s", (reading_id,)), current_user
    ):
        return api_error("Reading not found", 404)
    tx = db.fetch_one(
        "SELECT * FROM blockchain_transactions WHERE reading_id=%s ORDER BY id DESC LIMIT 1", (reading_id,)
    )
    transactions = db.fetch_all(
        "SELECT * FROM blockchain_transactions WHERE reading_id=%s ORDER BY id DESC LIMIT 5", (reading_id,)
    )
    return api_ok(
        status=row["status"],
        chain_mode=row["chain_mode"],
        tx_hash=row["chain_tx_hash"],
        block=row["chain_block"],
        chain_timestamp=json_default(row["chain_timestamp"]) if row["chain_timestamp"] else None,
        confirmations=row["chain_confirmations"],
        anchored_by=row["anchored_by"],
        explorer_url=_explorer(row["chain_tx_hash"], row["chain_mode"]),
        latest_transaction=tx,
        transactions=transactions,
    )


@api_bp.post("/readings/<int:reading_id>/anchor")
@api_login_required
def anchor_with_server(reading_id: int):
    reading = db.fetch_one(
        """SELECT r.*, h.chain_ref, h.meter_id FROM readings r
           JOIN households h ON h.id=r.household_id WHERE r.id=%s""",
        (reading_id,),
    )
    if not reading:
        return api_error("Reading not found", 404)
    household = get_household_or_none(reading["household_id"], current_user)
    if not household:
        return api_error("Reading not found", 404)
    if reading["status"] == "anchored":
        return api_error("This record is already anchored.", 409)
    recomputed = compute_record_hash(reading)
    if recomputed.lower() != reading["record_hash"].lower():
        db.execute("UPDATE readings SET status='tampered' WHERE id=%s", (reading_id,))
        return api_error("Integrity check failed. A modified record cannot be anchored.", 409)
    try:
        result = chain.submit_anchor(reading_id)
        audit.record("reading.anchor", entity="reading", entity_id=reading_id, details={"mode": chain.mode})
        return api_ok(**result, status="pending")
    except ChainError as exc:
        return api_error(str(exc), 503)


@api_bp.get("/readings/<int:reading_id>/anchor-prepare")
@api_login_required
def anchor_prepare(reading_id: int):
    reading = db.fetch_one(
        """SELECT r.*, h.chain_ref, h.household_code FROM readings r
           JOIN households h ON h.id=r.household_id WHERE r.id=%s""",
        (reading_id,),
    )
    if not reading:
        return api_error("Reading not found", 404)
    if not get_household_or_none(reading["household_id"], current_user):
        return api_error("Reading not found", 404)
    if reading["status"] == "anchored":
        return api_error("This record is already anchored.", 409)
    if not current_user.wallet_address or not current_user.wallet_verified:
        return api_error("Connect and verify a wallet in your profile before signing transactions.", 403)
    if chain.is_simulation:
        return api_error(
            "MetaMask anchoring requires BLOCKCHAIN_MODE=sepolia with a deployed contract.", 409
        )
    recomputed = compute_record_hash(reading)
    if recomputed.lower() != reading["record_hash"].lower():
        return api_error("Integrity check failed. This record has been modified.", 409)
    if not chain.is_recorder(current_user.wallet_address):
        return api_error(
            "Your wallet is not authorised on the contract. Ask an administrator to authorise it from the Users page.",
            403,
        )
    settings = get_settings()
    return api_ok(
        reading_id=reading_id,
        contract_address=current_app.config["CONTRACT_ADDRESS"],
        chain_id=current_app.config["SEPOLIA_CHAIN_ID"],
        chain_name="Ethereum Sepolia",
        explorer=current_app.config["EXPLORER_BASE_URL"],
        wallet=current_user.wallet_address,
        args={
            "householdRef": reading["chain_ref"],
            "sequence": int(reading["sequence"]),
            "consumptionMl": str(int(round(float(reading["consumption_l"]) * 1000))),
            "readingDate": int(
                datetime(
                    reading["reading_date"].year,
                    reading["reading_date"].month,
                    reading["reading_date"].day,
                    tzinfo=timezone.utc,
                ).timestamp()
            ),
            "recordHash": reading["record_hash"],
        },
        consumption_l=float(reading["consumption_l"]),
    )


@api_bp.post("/readings/<int:reading_id>/anchor-confirm")
@api_login_required
def anchor_confirm(reading_id: int):
    payload = request.get_json(silent=True) or {}
    tx_hash = (payload.get("tx_hash") or "").strip().lower()
    if not tx_hash:
        return api_error("tx_hash is required")
    reading = db.fetch_one(
        """SELECT r.*, h.chain_ref, h.meter_id, h.household_code FROM readings r
           JOIN households h ON h.id=r.household_id WHERE r.id=%s""",
        (reading_id,),
    )
    if not reading:
        return api_error("Reading not found", 404)
    household = get_household_or_none(reading["household_id"], current_user)
    if not household:
        return api_error("Reading not found", 404)
    if not current_user.wallet_address:
        return api_error("Link a wallet first.", 403)
    try:
        result = chain.confirm_metamask_tx(reading, household, tx_hash, current_user.wallet_address)
        if result["status"] == "confirmed":
            audit.record("reading.anchor_metamask", entity="reading", entity_id=reading_id,
                         details={"tx_hash": tx_hash, "wallet": current_user.wallet_address})
            return api_ok(**result)
        return api_ok(**result), 202
    except ChainError as exc:
        return api_error(str(exc), 400)
    except Exception as exc:
        current_app.logger.exception("MetaMask confirmation failed")
        return api_error(f"Could not verify transaction: {exc}", 500)


@api_bp.get("/blockchain/status")
@api_login_required
def blockchain_status():
    force = request.args.get("force") == "1"
    return api_ok(status=chain.status(force=force))


@api_bp.get("/blockchain/transactions")
@api_login_required
def blockchain_transactions():
    ids = accessible_household_ids(current_user)
    if not ids:
        return api_ok(transactions=[])
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except ValueError:
        limit = 50
    clause = f"r.household_id IN ({','.join(['%s'] * len(ids))})"
    params: list = list(ids)
    raw = request.args.get("household_id")
    if raw and raw.isdigit() and int(raw) in ids:
        clause += " AND r.household_id=%s"
        params.append(int(raw))
    rows = db.fetch_all(
        f"""SELECT t.*, r.reading_date, r.consumption_l, h.household_code
            FROM blockchain_transactions t
            JOIN readings r ON r.id=t.reading_id
            JOIN households h ON h.id=t.household_id
            WHERE {clause} ORDER BY t.id DESC LIMIT %s""",
        params + [limit],
    )
    return api_ok(
        transactions=[
            {
                **{k: json_default(v) if isinstance(v, (date, datetime)) or hasattr(v, "quantize") else v for k, v in row.items()},
                "explorer_url": _explorer(row["tx_hash"], row["chain_mode"]),
            }
            for row in rows
        ]
    )


@api_bp.get("/blockchain/audit")
@api_login_required
def blockchain_audit():
    onchain = request.args.get("onchain") == "1"
    persist = request.args.get("persist") == "1" and current_user.is_admin
    default_limit = 25 if onchain else 100
    try:
        limit = max(1, min(int(request.args.get("limit", default_limit)), 200))
    except ValueError:
        limit = default_limit
    result = audit_readings(current_user, verify_chain=onchain, persist=persist, limit=limit)
    simulation_chain = None
    if chain.is_simulation:
        simulation_chain = chain.verify_simulation_chain()
    return api_ok(audit=result, simulation_chain=simulation_chain, mode=chain.mode)


@api_bp.get("/reports/monthly")
@api_login_required
def reports_monthly():
    ids = _scope_households()
    if ids is None:
        return api_error("Household not found", 404)
    try:
        year = int(request.args.get("year", date.today().year))
    except ValueError:
        year = date.today().year
    if year < 2000 or year > date.today().year:
        return api_error("Invalid year")
    rows = monthly_report(ids, year)
    return api_ok(
        year=year,
        rows=rows,
        total_l=round(sum(row["total_l"] for row in rows), 2),
        total_cost=round(sum(row["cost"] for row in rows), 2),
    )


@api_bp.get("/contract")
@api_login_required
def contract_info():
    return api_ok(
        address=current_app.config["CONTRACT_ADDRESS"],
        chain_id=current_app.config["SEPOLIA_CHAIN_ID"],
        chain_id_hex=hex(current_app.config["SEPOLIA_CHAIN_ID"]),
        mode=chain.mode,
        explorer=current_app.config["EXPLORER_BASE_URL"],
        abi=chain.abi,
    )


@api_bp.get("/settings/limits")
@api_login_required
def limits_info():
    ids = _scope_households()
    if ids is None:
        return api_error("Household not found", 404)
    settings = get_settings()
    return api_ok(
        global_limits={
            "daily_limit_l": float(settings["daily_limit_l"]),
            "monthly_limit_l": float(settings["monthly_limit_l"]),
            "alert_threshold_pct": int(settings["alert_threshold_pct"]),
            "conservation_target_lpd": float(settings["conservation_target_lpd"]),
        },
        households=[{"id": hid, **resolve_limits(hid)} for hid in ids],
    )
