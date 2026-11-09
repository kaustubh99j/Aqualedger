"""MetaMask wallet linking, signature verification and on-chain authorisation."""
from __future__ import annotations

import secrets

from flask import Blueprint, current_app, request
from flask_login import current_user

from .. import db
from ..security import admin_required, api_login_required
from ..services import audit
from ..services.chain import ChainError, anchor_message, chain
from ..services.hashing import utc_now
from ..utils import WALLET_RE, api_error, api_ok

wallet_bp = Blueprint("wallet", __name__, url_prefix="/api/wallet")


def _valid_address(address: str | None) -> str | None:
    if not address:
        return None
    address = address.strip()
    return address if WALLET_RE.match(address) else None


@wallet_bp.get("/status")
@api_login_required
def status():
    authorized = None
    row = db.fetch_one("SELECT * FROM wallet_authorizations WHERE user_id=%s", (current_user.id,))
    if row:
        authorized = bool(row["authorized"])
        if chain.mode == "sepolia" and current_user.wallet_address:
            try:
                authorized = chain.is_recorder(current_user.wallet_address)
            except Exception:
                pass
    elif chain.is_simulation and current_user.wallet_address:
        authorized = True
    return api_ok(
        linked=bool(current_user.wallet_address),
        verified=bool(current_user.wallet_verified),
        address=current_user.wallet_address,
        authorized=authorized,
        mode=chain.mode,
        chain_name=chain.status().get("chain_name"),
        explorer=current_app.config["EXPLORER_BASE_URL"],
    )


@wallet_bp.get("/nonce")
@api_login_required
def nonce():
    address = _valid_address(request.args.get("address"))
    if not address:
        return api_error("Provide a valid Ethereum address")
    value = secrets.token_hex(16)
    db.execute("UPDATE users SET wallet_nonce=%s WHERE id=%s", (value, current_user.id))
    return api_ok(address=address, nonce=value, message=anchor_message(address, value))


@wallet_bp.post("/link")
@api_login_required
def link():
    payload = request.get_json(silent=True) or {}
    address = _valid_address(payload.get("address"))
    signature = (payload.get("signature") or "").strip()
    if not address:
        return api_error("Invalid Ethereum address")
    if not signature:
        return api_error("Wallet signature is required")

    row = db.fetch_one("SELECT wallet_nonce FROM users WHERE id=%s", (current_user.id,))
    stored_nonce = row["wallet_nonce"] if row else None
    if not stored_nonce:
        return api_error("Signature challenge expired. Request a new nonce and sign again.", 409)

    message = anchor_message(address, stored_nonce)
    if not chain.verify_wallet_signature(address, message, signature):
        audit.record("wallet.link_failed", entity="user", entity_id=current_user.id, details={"address": address})
        return api_error("Signature verification failed. Make sure you signed with the same account.", 400)

    other = db.fetch_one(
        "SELECT id FROM users WHERE wallet_address=%s AND id<>%s", (address, current_user.id)
    )
    if other:
        return api_error("This wallet is already linked to another account.", 409)

    db.execute(
        "UPDATE users SET wallet_address=%s, wallet_verified=1, wallet_nonce=NULL, wallet_linked_at=%s WHERE id=%s",
        (address, utc_now(), current_user.id),
    )
    audit.record("wallet.link", entity="user", entity_id=current_user.id, details={"address": address})
    return api_ok(
        address=address,
        verified=True,
        message="Wallet linked successfully. An administrator must authorise it on the contract before you can sign anchoring transactions.",
    )


@wallet_bp.post("/unlink")
@api_login_required
def unlink():
    address = current_user.wallet_address
    db.execute(
        "UPDATE users SET wallet_address=NULL, wallet_verified=0, wallet_nonce=NULL, wallet_linked_at=NULL WHERE id=%s",
        (current_user.id,),
    )
    db.execute(
        "UPDATE wallet_authorizations SET authorized=0 WHERE user_id=%s AND authorized=1", (current_user.id,)
    )
    audit.record("wallet.unlink", entity="user", entity_id=current_user.id, details={"address": address})
    return api_ok(message="Wallet unlinked.")


@wallet_bp.post("/authorize")
@api_login_required
@admin_required
def authorize():
    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id")
    allowed = bool(payload.get("allowed", True))
    if not str(user_id).isdigit():
        return api_error("user_id is required")
    user = db.fetch_one("SELECT * FROM users WHERE id=%s", (int(user_id),))
    if not user:
        return api_error("User not found", 404)
    if not user["wallet_address"] or not user["wallet_verified"]:
        return api_error("This user has not linked a verified wallet yet.", 409)

    try:
        result = chain.set_recorder(user["wallet_address"], allowed)
    except ChainError as exc:
        return api_error(str(exc), 503)
    except Exception as exc:
        current_app.logger.exception("Wallet authorisation failed")
        return api_error(f"On-chain authorisation failed: {exc}", 500)

    db.execute(
        """INSERT INTO wallet_authorizations
           (user_id, wallet_address, authorized, tx_hash, chain_mode, updated_by)
           VALUES (%s,%s,%s,%s,%s,%s)
           ON DUPLICATE KEY UPDATE wallet_address=VALUES(wallet_address), authorized=VALUES(authorized),
             tx_hash=VALUES(tx_hash), chain_mode=VALUES(chain_mode), updated_by=VALUES(updated_by)""",
        (
            user["id"],
            user["wallet_address"],
            1 if allowed else 0,
            result.get("tx_hash"),
            chain.mode,
            current_user.id,
        ),
    )
    audit.record(
        "wallet.authorize" if allowed else "wallet.revoke",
        entity="user",
        entity_id=user["id"],
        details={"wallet": user["wallet_address"], "tx_hash": result.get("tx_hash"), "mode": chain.mode},
    )
    verb = "authorised" if allowed else "revoked"
    return api_ok(
        message=f"Wallet {verb} on the contract.",
        tx_hash=result.get("tx_hash"),
        mode=chain.mode,
        explorer=(
            f"{current_app.config['EXPLORER_BASE_URL'].rstrip('/')}/tx/{result.get('tx_hash')}"
            if result.get("tx_hash") and chain.mode == "sepolia"
            else None
        ),
    )
