"""Unusual-usage detection and alert persistence."""
from __future__ import annotations

import logging
from datetime import timedelta

from .. import db
from .hashing import utc_now
from .settings import resolve_limits

logger = logging.getLogger(__name__)


def create_alert(
    household_id: int,
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    *,
    reading_id: int | None = None,
    value_l: float | None = None,
    threshold_l: float | None = None,
) -> int | None:
    """Insert an alert, suppressing exact duplicates."""
    existing = db.fetch_one(
        """SELECT id FROM alerts
           WHERE household_id=%s AND alert_type=%s
             AND ((%s IS NULL AND reading_id IS NULL) OR reading_id=%s)
             AND created_at >= %s
           LIMIT 1""",
        (household_id, alert_type, reading_id, reading_id, utc_now() - timedelta(days=45)),
    )
    if existing:
        return None
    return db.execute(
        """INSERT INTO alerts (household_id, reading_id, alert_type, severity, title, message,
                               value_l, threshold_l, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (household_id, reading_id, alert_type, severity, title, message, value_l, threshold_l, utc_now()),
    )


def evaluate_reading(reading: dict, household: dict) -> list[dict]:
    """Run every usage rule against a newly inserted reading.

    Returns the list of alerts that were created so the UI can surface them.
    """
    limits = resolve_limits(household["id"])
    created: list[dict] = []
    consumption = float(reading["consumption_l"])
    daily_limit = limits["daily_limit_l"]
    monthly_limit = limits["monthly_limit_l"]
    threshold_pct = limits["alert_threshold_pct"]

    def add(alert_type, severity, title, message, value=None, threshold=None):
        alert_id = create_alert(
            household["id"],
            alert_type,
            severity,
            title,
            message,
            reading_id=reading["id"],
            value_l=value if value is not None else consumption,
            threshold_l=threshold,
        )
        if alert_id:
            created.append(
                {
                    "id": alert_id,
                    "type": alert_type,
                    "severity": severity,
                    "title": title,
                    "message": message,
                }
            )

    limit_floor = max(daily_limit, 1)
    pct_of_daily = consumption / limit_floor * 100

    if consumption > daily_limit:
        add(
            "daily_limit",
            "critical",
            "Daily limit exceeded",
            f"{household['household_code']} used {consumption:,.0f} L on {reading['reading_date']}, "
            f"which is {consumption - daily_limit:,.0f} L above the {daily_limit:,.0f} L daily limit.",
            threshold=daily_limit,
        )
    elif pct_of_daily >= threshold_pct:
        add(
            "daily_limit",
            "warning",
            "Approaching daily limit",
            f"{household['household_code']} has consumed {consumption:,.0f} L "
            f"({pct_of_daily:.0f}% of the {daily_limit:,.0f} L daily limit) on {reading['reading_date']}.",
            threshold=daily_limit,
        )

    month_start = reading["reading_date"].replace(day=1)
    month_total = db.fetch_value(
        """SELECT COALESCE(SUM(consumption_l),0) FROM readings
           WHERE household_id=%s AND reading_date >= %s AND reading_date <= %s""",
        (household["id"], month_start, reading["reading_date"]),
        default=0,
    )
    month_total = float(month_total or 0)
    month_pct = month_total / max(monthly_limit, 1) * 100
    if month_total > monthly_limit:
        add(
            "monthly_limit",
            "critical",
            "Monthly limit exceeded",
            f"Month-to-date usage is {month_total:,.0f} L against a {monthly_limit:,.0f} L monthly limit.",
            value=month_total,
            threshold=monthly_limit,
        )
    elif month_pct >= threshold_pct:
        existing_month = db.fetch_one(
            """SELECT id FROM alerts WHERE household_id=%s AND alert_type='monthly_limit'
               AND created_at >= %s LIMIT 1""",
            (household["id"], utc_now() - timedelta(days=45)),
        )
        if not existing_month:
            add(
                "monthly_limit",
                "warning",
                "Approaching monthly limit",
                f"Month-to-date usage is {month_total:,.0f} L ({month_pct:.0f}% of the "
                f"{monthly_limit:,.0f} L monthly limit).",
                value=month_total,
                threshold=monthly_limit,
            )

    # Sudden spike rule: today vs the previous 14 recorded days.
    history = db.fetch_all(
        """SELECT consumption_l FROM readings
           WHERE household_id=%s AND reading_date < %s
           ORDER BY reading_date DESC LIMIT 14""",
        (household["id"], reading["reading_date"]),
    )
    if len(history) >= 5:
        avg = sum(float(row["consumption_l"]) for row in history) / len(history)
        if avg > 0 and consumption >= avg * 2.5 and consumption >= 200:
            add(
                "usage_spike",
                "warning",
                "Unusual usage spike detected",
                f"Consumption on {reading['reading_date']} was {consumption:,.0f} L, "
                f"{consumption / avg:.1f}x the recent 14-day average of {avg:,.0f} L.",
                threshold=round(avg * 2.5, 2),
            )

    return created


def notify_tamper(reading: dict, household: dict, detail: str) -> None:
    create_alert(
        household["id"],
        "tamper",
        "critical",
        "Record integrity failure",
        f"Reading #{reading['id']} for {household['household_code']} on {reading['reading_date']} failed integrity "
        f"verification. {detail}",
        reading_id=reading["id"],
    )
