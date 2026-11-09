"""Reading creation with meter-chain and integrity guarantees."""
from __future__ import annotations

from datetime import date

from .. import db
from ..utils import ValidationError, parse_date, parse_float
from .alerts import evaluate_reading
from .hashing import compute_record_hash, utc_now


class ReadingError(ValidationError):
    pass


def latest_reading(household_id: int) -> dict | None:
    return db.fetch_one(
        "SELECT * FROM readings WHERE household_id=%s ORDER BY reading_date DESC, id DESC LIMIT 1",
        (household_id,),
    )


def create_reading(
    household: dict,
    *,
    reading_date: str | date | None,
    current_reading_m3,
    previous_reading_m3=None,
    notes: str | None = None,
    source: str = "manual",
    recorded_by: int | None = None,
) -> dict:
    """Insert a reading inside a row-locked transaction.

    Returns the inserted reading (with household fields) plus triggered alerts.
    """
    if household["status"] != "active":
        raise ReadingError("This household is inactive. Reactivate it before recording readings.")
    day = parse_date(reading_date, "Reading date") if isinstance(reading_date, str) else reading_date
    if day is None:
        raise ReadingError("Reading date is required.")
    if day > date.today():
        raise ReadingError("Reading date cannot be in the future.")

    current = parse_float(current_reading_m3, "Current meter reading", minimum=0, maximum=1000000)
    if source not in ("manual", "estimated"):
        source = "manual"
    notes = (notes or "").strip()[:255] or None

    with db.transaction() as cur:
        cur.execute("SELECT * FROM households WHERE id=%s FOR UPDATE", (household["id"],))
        locked = cur.fetchone()
        if not locked:
            raise ReadingError("Household not found.")

        cur.execute(
            "SELECT * FROM readings WHERE household_id=%s ORDER BY reading_date DESC, id DESC LIMIT 1 FOR UPDATE",
            (household["id"],),
        )
        latest = cur.fetchone()

        if latest:
            if day == latest["reading_date"]:
                raise ReadingError(f"A reading for {day.isoformat()} already exists. Edit is disabled to preserve the proof chain.")
            if day < latest["reading_date"]:
                raise ReadingError(
                    f"Readings must move forward in time. The latest reading is {latest['reading_date']}. "
                    "Back-dated readings would break the meter chain."
                )
            previous = float(latest["current_reading_m3"])
        else:
            if previous_reading_m3 is None or str(previous_reading_m3).strip() == "":
                raise ReadingError(
                    "This is the first reading for the household. Enter the meter's starting reading as the previous value."
                )
            previous = parse_float(previous_reading_m3, "Previous meter reading", minimum=0, maximum=1000000)

        if current < previous:
            raise ReadingError(
                f"Current reading ({current:.3f} m³) cannot be lower than the previous reading ({previous:.3f} m³)."
            )
        consumption = round((current - previous) * 1000, 2)
        if consumption > 100000:
            raise ReadingError("Consumption exceeds 100,000 L for a single day. Check the meter values.")

        sequence = int(latest["sequence"]) + 1 if latest else 1
        created = utc_now()
        record = {
            "chain_ref": locked["chain_ref"],
            "meter_id": locked["meter_id"],
            "sequence": sequence,
            "reading_date": day,
            "previous_reading_m3": round(previous, 3),
            "current_reading_m3": round(current, 3),
            "consumption_l": consumption,
            "created_at": created,
        }
        digest = compute_record_hash(record)

        cur.execute(
            """INSERT INTO readings
               (household_id, sequence, reading_date, previous_reading_m3, current_reading_m3,
                consumption_l, reading_source, notes, recorded_by, record_hash, status, chain_mode, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending','simulation',%s)""",
            (
                household["id"],
                sequence,
                day,
                record["previous_reading_m3"],
                record["current_reading_m3"],
                consumption,
                source,
                notes,
                recorded_by,
                digest,
                created,
            ),
        )
        reading_id = cur.lastrowid

    reading = db.fetch_one(
        """SELECT r.*, h.chain_ref, h.meter_id, h.household_code, h.name AS household_name,
                  h.members_count
           FROM readings r JOIN households h ON h.id=r.household_id WHERE r.id=%s""",
        (reading_id,),
    )
    alerts = evaluate_reading(reading, household)
    return {"reading": reading, "alerts": alerts, "record_hash": digest}
