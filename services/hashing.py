"""Canonical record hashing utilities.

The canonical string is documented in README.md and implemented identically in
scripts/verify_chain.py so third parties can re-compute a digest from an
exported record and compare it with the value stored on Ethereum.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime, timezone

from eth_utils import keccak

HASH_VERSION = "AquaLedger:v1"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_payload(
    *,
    household_ref: str,
    sequence: int,
    meter_id: str,
    reading_date: date | str,
    previous_reading_m3,
    current_reading_m3,
    consumption_l,
    created_at: datetime | str,
) -> str:
    """Build the deterministic payload that gets hashed.

    Floats are intentionally avoided: values are formatted with fixed precision
    so the digest is stable across Python, MySQL and any external verifier.
    """
    if isinstance(reading_date, date):
        reading_date = reading_date.isoformat()
    if isinstance(created_at, datetime):
        created_at = iso_utc(created_at)
    return "|".join(
        [
            HASH_VERSION,
            household_ref.lower(),
            str(int(sequence)),
            str(meter_id).strip(),
            str(reading_date),
            f"{float(previous_reading_m3):.3f}",
            f"{float(current_reading_m3):.3f}",
            f"{float(consumption_l):.2f}",
            str(created_at),
        ]
    )


def record_hash(payload: str) -> str:
    """keccak256 of the UTF-8 canonical payload, 0x-prefixed hex."""
    return "0x" + keccak(text=payload).hex()


def compute_record_hash(record: dict) -> str:
    payload = canonical_payload(
        household_ref=record["chain_ref"],
        sequence=record["sequence"],
        meter_id=record["meter_id"],
        reading_date=record["reading_date"],
        previous_reading_m3=record["previous_reading_m3"],
        current_reading_m3=record["current_reading_m3"],
        consumption_l=record["consumption_l"],
        created_at=record["created_at"],
    )
    return record_hash(payload)


def new_household_ref() -> str:
    """Random 32-byte reference; not derived from any personal data."""
    return "0x" + secrets.token_hex(32)


def short_hash(value: str | None, head: int = 10, tail: int = 8) -> str:
    if not value:
        return "--"
    if len(value) <= head + tail + 3:
        return value
    return f"{value[:head]}...{value[-tail:]}"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def to_unix_date(value: date | datetime) -> int:
    if isinstance(value, datetime):
        value = value.date()
    dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return int(dt.timestamp())
