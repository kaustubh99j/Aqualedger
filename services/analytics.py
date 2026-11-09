"""Consumption analytics, dashboards, reports and integrity auditing."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from .. import db
from ..utils import add_months, last_n_days, month_bounds
from .alerts import notify_tamper
from .hashing import compute_record_hash, utc_now
from .settings import get_settings, resolve_limits

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- access
def accessible_household_ids(user) -> list[int]:
    if user.is_admin:
        rows = db.fetch_all("SELECT id FROM households WHERE status='active'")
    else:
        rows = db.fetch_all(
            "SELECT id FROM households WHERE user_id=%s AND status='active'", (user.id,)
        )
    return [int(row["id"]) for row in rows]


def get_household_or_none(household_id: int, user) -> dict | None:
    row = db.fetch_one(
        """SELECT h.*, u.name AS owner_name, u.email AS owner_email
           FROM households h LEFT JOIN users u ON u.id = h.user_id WHERE h.id=%s""",
        (household_id,),
    )
    if not row:
        return None
    if not user.is_admin and int(row["user_id"] or 0) != int(user.id):
        return None
    return row


def _placeholders(values: list) -> str:
    return ",".join(["%s"] * len(values))


def _scope_clause(household_ids: list[int] | None, column: str = "household_id") -> tuple[str, list]:
    if household_ids is None:
        return "1=1", []
    if not household_ids:
        return "1=0", []
    return f"{column} IN ({_placeholders(household_ids)})", list(household_ids)


# ------------------------------------------------------------------ aggregates
def consumption_between(household_ids: list[int], start: date, end: date) -> float:
    clause, params = _scope_clause(household_ids)
    value = db.fetch_value(
        f"SELECT COALESCE(SUM(consumption_l),0) AS total FROM readings "
        f"WHERE {clause} AND reading_date BETWEEN %s AND %s",
        params + [start, end],
        default=0,
    )
    return float(value or 0)


def daily_series(household_ids: list[int], days: int = 30, end: date | None = None) -> dict:
    end = end or date.today()
    start = end - timedelta(days=days - 1)
    clause, params = _scope_clause(household_ids)
    rows = db.fetch_all(
        f"""SELECT reading_date, SUM(consumption_l) AS total, COUNT(*) AS entries
            FROM readings WHERE {clause} AND reading_date BETWEEN %s AND %s
            GROUP BY reading_date ORDER BY reading_date""",
        params + [start, end],
    )
    by_day = {row["reading_date"]: float(row["total"]) for row in rows}
    labels, values, entries = [], [], []
    for day in last_n_days(days, end):
        labels.append(day.isoformat())
        values.append(round(by_day.get(day, 0), 2))
        entries.append(1 if day in by_day else 0)
    return {"labels": labels, "values": values, "entries": entries, "start": start, "end": end}


def monthly_series(household_ids: list[int], months: int = 12, end: date | None = None) -> dict:
    end = end or date.today()
    start_month = add_months(end.replace(day=1), -(months - 1))
    clause, params = _scope_clause(household_ids)
    rows = db.fetch_all(
        f"""SELECT DATE_FORMAT(reading_date, '%%Y-%%m') AS ym, SUM(consumption_l) AS total
            FROM readings WHERE {clause} AND reading_date >= %s AND reading_date <= %s
            GROUP BY ym ORDER BY ym""",
        params + [start_month, end],
    )
    by_month = {row["ym"]: float(row["total"]) for row in rows}
    labels, values = [], []
    cursor = start_month
    while cursor <= end:
        key = cursor.strftime("%Y-%m")
        labels.append(key)
        values.append(round(by_month.get(key, 0), 2))
        cursor = add_months(cursor, 1)
    return {"labels": labels, "values": values, "start": start_month, "end": end}


def today_consumption(household_ids: list[int]) -> float:
    return consumption_between(household_ids, date.today(), date.today())


def week_consumption(household_ids: list[int]) -> float:
    today = date.today()
    return consumption_between(household_ids, today - timedelta(days=6), today)


def month_consumption(household_ids: list[int]) -> float:
    start, end = month_bounds(date.today())
    return consumption_between(household_ids, start, min(end, date.today()))


def limit_total(household_ids: list[int], kind: str = "daily") -> float:
    total = 0.0
    for household_id in household_ids:
        limits = resolve_limits(household_id)
        total += limits[f"{kind}_limit_l"]
    return total


def average_daily(household_ids: list[int], days: int = 30) -> float:
    end = date.today()
    start = end - timedelta(days=days - 1)
    clause, params = _scope_clause(household_ids)
    row = db.fetch_one(
        f"""SELECT COALESCE(SUM(consumption_l),0) AS total, COUNT(DISTINCT reading_date) AS days
            FROM readings WHERE {clause} AND reading_date BETWEEN %s AND %s""",
        params + [start, end],
    )
    if not row or not int(row["days"] or 0):
        return 0.0
    return round(float(row["total"]) / int(row["days"]), 2)


# ---------------------------------------------------------------------- score
def conservation_score(household: dict, target_lpd: float | None = None, days: int = 30) -> dict:
    settings = get_settings()
    target = float(target_lpd if target_lpd is not None else settings["conservation_target_lpd"])
    members = max(int(household["members_count"] or 1), 1)
    baseline = target * members

    today = date.today()
    start = today - timedelta(days=days - 1)
    row = db.fetch_one(
        """SELECT COALESCE(SUM(consumption_l),0) AS total, COUNT(DISTINCT reading_date) AS days
           FROM readings WHERE household_id=%s AND reading_date BETWEEN %s AND %s""",
        (household["id"], start, today),
    )
    recorded_days = int(row["days"] or 0) if row else 0
    if not recorded_days:
        return {
            "score": None,
            "grade": "--",
            "avg_daily_l": 0,
            "baseline_l": round(baseline, 1),
            "ratio": 0,
            "recorded_days": 0,
            "trend_pct": None,
            "label": "Not enough data",
        }

    avg = float(row["total"]) / recorded_days
    ratio = avg / baseline if baseline else 0
    if ratio <= 0.6:
        score = 100
    elif ratio <= 1.0:
        score = 100 - ((ratio - 0.6) / 0.4) * 25
    elif ratio <= 1.5:
        score = 75 - ((ratio - 1.0) / 0.5) * 35
    else:
        score = max(0, 40 - (ratio - 1.5) * 40)

    previous_start = start - timedelta(days=days)
    previous_end = start - timedelta(days=1)
    prev = db.fetch_value(
        """SELECT COALESCE(SUM(consumption_l),0) / NULLIF(COUNT(DISTINCT reading_date),0)
           FROM readings WHERE household_id=%s AND reading_date BETWEEN %s AND %s""",
        (household["id"], previous_start, previous_end),
        default=None,
    )
    trend = None
    if prev:
        prev = float(prev)
        if prev > 0:
            trend = round((avg - prev) / prev * 100, 1)
            if trend <= -10:
                score += 5
            elif trend >= 10:
                score -= 5
    score = int(round(max(0, min(100, score))))
    if score >= 90:
        grade, label = "A+", "Excellent conservation"
    elif score >= 80:
        grade, label = "A", "Very efficient"
    elif score >= 70:
        grade, label = "B", "Efficient"
    elif score >= 60:
        grade, label = "C", "Needs attention"
    elif score >= 50:
        grade, label = "D", "High usage"
    else:
        grade, label = "E", "Critical usage"

    return {
        "score": score,
        "grade": grade,
        "avg_daily_l": round(avg, 1),
        "baseline_l": round(baseline, 1),
        "ratio": round(ratio, 2),
        "recorded_days": recorded_days,
        "trend_pct": trend,
        "label": label,
    }


def score_summary(household_ids: list[int]) -> dict:
    households = db.fetch_all(
        f"SELECT * FROM households WHERE id IN ({_placeholders(household_ids)}) ORDER BY household_code",
        household_ids,
    ) if household_ids else []
    scores = []
    for household in households:
        info = conservation_score(household)
        if info["score"] is not None:
            scores.append(info["score"])
    aggregate = int(round(sum(scores) / len(scores))) if scores else None
    if aggregate is None:
        grade = "--"
    elif aggregate >= 90:
        grade = "A+"
    elif aggregate >= 80:
        grade = "A"
    elif aggregate >= 70:
        grade = "B"
    elif aggregate >= 60:
        grade = "C"
    elif aggregate >= 50:
        grade = "D"
    else:
        grade = "E"
    return {"score": aggregate, "grade": grade, "households": len(households)}


# ------------------------------------------------------------------ dashboard
def dashboard_stats(user) -> dict:
    household_ids = accessible_household_ids(user)
    today = date.today()
    month_start, month_end = month_bounds(today)
    yesterday = consumption_between(household_ids, today - timedelta(days=1), today - timedelta(days=1))
    today_l = today_consumption(household_ids)
    change = round((today_l - yesterday) / yesterday * 100, 1) if yesterday > 0 else None

    status_counts = db.fetch_one(
        f"""SELECT
              SUM(status='anchored') AS anchored,
              SUM(status='pending') AS pending,
              SUM(status='failed') AS failed,
              SUM(status='tampered') AS tampered
            FROM readings WHERE household_id IN ({_placeholders(household_ids)})"""
        if household_ids
        else "SELECT 0 AS anchored, 0 AS pending, 0 AS failed, 0 AS tampered",
        household_ids if household_ids else [],
    ) or {}

    last_reading = db.fetch_one(
        f"""SELECT r.*, h.household_code FROM readings r JOIN households h ON h.id=r.household_id
            WHERE r.household_id IN ({_placeholders(household_ids)})
            ORDER BY r.reading_date DESC, r.id DESC LIMIT 1"""
        if household_ids
        else "SELECT NULL AS id LIMIT 0",
        household_ids if household_ids else [],
    )

    active_alerts = db.fetch_value(
        f"""SELECT COUNT(*) FROM alerts WHERE household_id IN ({_placeholders(household_ids)})
            AND is_read=0 AND resolved_at IS NULL"""
        if household_ids
        else "SELECT 0",
        household_ids if household_ids else [],
        default=0,
    )

    return {
        "household_count": len(household_ids),
        "today_l": round(today_l, 2),
        "week_l": round(week_consumption(household_ids), 2),
        "month_l": round(month_consumption(household_ids), 2),
        "yesterday_l": round(yesterday, 2),
        "change_pct": change,
        "avg_daily_l": average_daily(household_ids),
        "daily_limit_l": round(limit_total(household_ids, "daily"), 2),
        "monthly_limit_l": round(limit_total(household_ids, "monthly"), 2),
        "active_alerts": int(active_alerts or 0),
        "anchored": int(status_counts.get("anchored") or 0),
        "pending": int(status_counts.get("pending") or 0),
        "failed": int(status_counts.get("failed") or 0),
        "tampered": int(status_counts.get("tampered") or 0),
        "score": score_summary(household_ids),
        "last_reading": last_reading,
        "month_start": month_start,
        "month_end": month_end,
    }


# -------------------------------------------------------------------- reports
def monthly_report(household_ids: list[int], year: int) -> list[dict]:
    settings = get_settings()
    tariff = float(settings["tariff_per_kl"])
    rows = []
    for month in range(1, 13):
        start = date(year, month, 1)
        end = min(month_bounds(start)[1], date.today()) if year == date.today().year else month_bounds(start)[1]
        if year == date.today().year and start > date.today():
            break
        clause, params = _scope_clause(household_ids)
        agg = db.fetch_one(
            f"""SELECT COALESCE(SUM(consumption_l),0) AS total, COUNT(DISTINCT reading_date) AS days,
                       COUNT(*) AS entries
                FROM readings WHERE {clause} AND reading_date BETWEEN %s AND %s""",
            params + [start, end],
        ) or {}
        total = float(agg.get("total") or 0)
        days = int(agg.get("days") or 0)
        entries = int(agg.get("entries") or 0)
        limit = 0.0
        for household_id in household_ids:
            limits = resolve_limits(household_id)
            limit += limits["monthly_limit_l"]
        if not entries:
            status = "No data"
        elif total > limit > 0:
            status = "Exceeded"
        else:
            status = "Within limit"
        rows.append(
            {
                "month": start.strftime("%b %Y"),
                "month_key": start.strftime("%Y-%m"),
                "total_l": round(total, 2),
                "avg_daily_l": round(total / days, 1) if days else 0,
                "entries": entries,
                "limit_l": round(limit, 2),
                "pct_of_limit": round(total / limit * 100, 1) if limit else 0,
                "cost": round(total / 1000 * tariff, 2),
                "status": status,
            }
        )
    return rows


# -------------------------------------------------------------------- integrity
def audit_readings(user, *, verify_chain: bool = False, persist: bool = False, limit: int = 500) -> dict:
    household_ids = accessible_household_ids(user)
    if not household_ids:
        return {"readings": [], "summary": {"total": 0, "valid": 0, "tampered": 0, "pending": 0}}
    clause, params = _scope_clause(household_ids)
    rows = db.fetch_all(
        f"""SELECT r.*, h.chain_ref, h.meter_id, h.household_code, h.name AS household_name
            FROM readings r JOIN households h ON h.id=r.household_id
            WHERE {clause} ORDER BY r.reading_date DESC, r.id DESC LIMIT %s""",
        params + [limit],
    )
    chain = None
    if verify_chain:
        from .chain import chain as chain_service

        chain = chain_service

    results = []
    tampered = 0
    pending = 0
    valid = 0
    for row in rows:
        recomputed = compute_record_hash(row)
        db_match = recomputed.lower() == row["record_hash"].lower()
        chain_result = None
        if db_match and row["status"] == "anchored":
            valid += 1
        elif not db_match:
            tampered += 1
        if row["status"] in ("pending", "failed"):
            pending += 1

        if verify_chain and chain and row["status"] == "anchored":
            chain_result = chain.verify_record(row, row, recomputed)
            if not chain_result["hash_matches"]:
                tampered += 1
                valid = max(0, valid - 1)
                db_match = False

        results.append(
            {
                "id": row["id"],
                "household_id": row["household_id"],
                "household_code": row["household_code"],
                "household_name": row["household_name"],
                "reading_date": row["reading_date"],
                "consumption_l": float(row["consumption_l"]),
                "stored_hash": row["record_hash"],
                "recomputed_hash": recomputed,
                "db_match": db_match,
                "status": row["status"],
                "chain_mode": row["chain_mode"],
                "chain_tx_hash": row["chain_tx_hash"],
                "chain_result": chain_result,
            }
        )

        if persist and not db_match and row["status"] != "tampered":
            db.execute("UPDATE readings SET status='tampered' WHERE id=%s", (row["id"],))
            notify_tamper(
                row,
                row,
                "The stored record digest does not match the digest recomputed from current database values.",
            )

    return {
        "readings": results,
        "summary": {
            "total": len(results),
            "valid": valid,
            "tampered": tampered,
            "pending": pending,
            "checked_on_chain": verify_chain,
        },
    }


def record_consumption_history(household_ids: list[int], days: int = 30) -> list[dict]:
    clause, params = _scope_clause(household_ids)
    start = date.today() - timedelta(days=days - 1)
    return db.fetch_all(
        f"""SELECT r.*, h.household_code, h.name AS household_name
            FROM readings r JOIN households h ON h.id=r.household_id
            WHERE {clause} AND r.reading_date >= %s
            ORDER BY r.reading_date DESC, r.id DESC""",
        params + [start],
    )
