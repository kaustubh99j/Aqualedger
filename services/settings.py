"""Typed accessors for global settings and per-household limits."""
from __future__ import annotations

from .. import db


DEFAULTS = {
    "daily_limit_l": 600.0,
    "monthly_limit_l": 18000.0,
    "alert_threshold_pct": 80,
    "conservation_target_lpd": 135.0,
    "tariff_per_kl": 25.0,
    "auto_anchor": "1",
}


def get_settings() -> dict:
    rows = db.fetch_all("SELECT setting_key, setting_value FROM settings")
    values = dict(DEFAULTS)
    if rows:
        values.update({row["setting_key"]: row["setting_value"] for row in rows})
    return values


def get_setting(key: str, default=None):
    return get_settings().get(key, default)


def get_float(key: str, default: float) -> float:
    try:
        return float(get_settings().get(key, default))
    except (TypeError, ValueError):
        return default


def get_int(key: str, default: int) -> int:
    try:
        return int(float(get_settings().get(key, default)))
    except (TypeError, ValueError):
        return default


def update_settings(values: dict, user_id: int | None = None) -> None:
    allowed = {
        "daily_limit_l",
        "monthly_limit_l",
        "alert_threshold_pct",
        "conservation_target_lpd",
        "tariff_per_kl",
        "auto_anchor",
    }
    for key, value in values.items():
        if key in allowed:
            db.execute(
                "INSERT INTO settings (setting_key, setting_value) VALUES (%s,%s) "
                "ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)",
                (key, str(value)),
            )


def resolve_limits(household_id: int) -> dict:
    settings = get_settings()
    override = db.fetch_one("SELECT * FROM household_limits WHERE household_id=%s", (household_id,))
    if override:
        return {
            "daily_limit_l": float(override["daily_limit_l"]),
            "monthly_limit_l": float(override["monthly_limit_l"]),
            "alert_threshold_pct": int(override["alert_threshold_pct"]),
            "source": "household",
        }
    return {
        "daily_limit_l": float(settings["daily_limit_l"]),
        "monthly_limit_l": float(settings["monthly_limit_l"]),
        "alert_threshold_pct": int(settings["alert_threshold_pct"]),
        "source": "global",
    }


def set_household_limits(
    household_id: int, daily: float, monthly: float, threshold: int, user_id: int
) -> None:
    db.execute(
        """INSERT INTO household_limits (household_id, daily_limit_l, monthly_limit_l, alert_threshold_pct, updated_by)
           VALUES (%s,%s,%s,%s,%s)
           ON DUPLICATE KEY UPDATE daily_limit_l=VALUES(daily_limit_l),
             monthly_limit_l=VALUES(monthly_limit_l), alert_threshold_pct=VALUES(alert_threshold_pct),
             updated_by=VALUES(updated_by)""",
        (household_id, daily, monthly, threshold, user_id),
    )


def clear_household_limits(household_id: int) -> None:
    db.execute("DELETE FROM household_limits WHERE household_id=%s", (household_id,))
