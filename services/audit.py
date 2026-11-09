"""Application audit trail (off-chain accountability log)."""
from __future__ import annotations

import json

from flask import has_request_context, request

from .. import db


def record(
    action: str,
    *,
    entity: str | None = None,
    entity_id=None,
    details: dict | str | None = None,
    user_id: int | None = None,
) -> None:
    if user_id is None and has_request_context():
        try:
            from flask_login import current_user

            if current_user.is_authenticated:
                user_id = current_user.id
        except Exception:
            user_id = None
    ip = request.remote_addr if has_request_context() else None
    if isinstance(details, (dict, list)):
        details = json.dumps(details, default=str)
    try:
        db.execute(
            """INSERT INTO audit_log (user_id, action, entity, entity_id, details, ip_address)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (user_id, action, entity, str(entity_id) if entity_id is not None else None, details, ip),
        )
    except Exception:
        # Auditing must never break the user-facing operation.
        pass
