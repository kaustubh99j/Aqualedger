"""Blockchain integration.

Modes
-----
``sepolia``    Real Ethereum Sepolia anchoring through Web3.py and the deployed
               AquaLedger contract. The backend recorder key signs transactions
               automatically; resident wallets can also sign in MetaMask and the
               backend verifies the resulting receipt before trusting it.

``simulation`` Offline fallback for evaluation machines without Sepolia funds.
               It writes a deterministic keccak hash-chain to MySQL. It is *not*
               a local blockchain node and is clearly labelled in the UI.

No personal data ever leaves MySQL. Only a random household reference, a
sequence number, consumption in millilitres, the reading date and the record
digest are sent to the contract.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
from web3.exceptions import TimeExhausted

from .. import db
from .hashing import iso_utc, to_unix_date, utc_now

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
ABI_PATH = BASE_DIR / "abi" / "AquaLedger.json"
ZERO_HASH = "0x" + "00" * 32

_active_lock = threading.Lock()
_active_anchors: set[int] = set()


class ChainError(RuntimeError):
    pass


class ChainService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._w3: Web3 | None = None
        self._w3_checked = 0.0
        self._status_cache: dict | None = None
        self._status_cached_at = 0.0
        self.config = None
        self.abi: list = []
        self.mode = "simulation"
        self.contract_address = ""
        self.recorder_key = ""
        self.recorder_address = ""
        self._load_abi()

    # ------------------------------------------------------------------ setup
    def init_app(self, app) -> None:
        self.config = app.config
        self.mode = app.config["BLOCKCHAIN_MODE"]
        self.contract_address = app.config["CONTRACT_ADDRESS"]
        self.recorder_key = app.config["RECORDER_PRIVATE_KEY"]
        if self.recorder_key:
            try:
                key = self.recorder_key if self.recorder_key.startswith("0x") else "0x" + self.recorder_key
                self.recorder_address = Account.from_key(key).address
            except Exception as exc:  # pragma: no cover - config error
                logger.error("Invalid RECORDER_PRIVATE_KEY: %s", exc)
                self.recorder_address = ""

    def _load_abi(self) -> None:
        try:
            with open(ABI_PATH, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            self.abi = payload["abi"]
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            logger.error("Could not load contract ABI: %s", exc)

    @property
    def is_simulation(self) -> bool:
        return self.mode != "sepolia"

    @property
    def is_configured(self) -> bool:
        if self.is_simulation:
            return True
        return bool(self.contract_address and self.recorder_key and self.config)

    def _web3(self) -> Web3:
        if not self.config:
            raise ChainError("Blockchain service not initialised")
        now = time.time()
        if self._w3 is None or now - self._w3_checked > 60:
            self._w3 = Web3(
                Web3.HTTPProvider(
                    self.config["SEPOLIA_RPC_URL"],
                    request_kwargs={"timeout": 25},
                )
            )
            self._w3_checked = now
        if not self._w3.is_connected():
            raise ChainError(
                "Cannot reach the Sepolia RPC endpoint. Check SEPOLIA_RPC_URL and your internet connection."
            )
        return self._w3

    def _contract(self):
        w3 = self._web3()
        if not self.contract_address:
            raise ChainError("CONTRACT_ADDRESS is not set. Deploy AquaLedger.sol in Remix first.")
        if not Web3.is_address(self.contract_address):
            raise ChainError("CONTRACT_ADDRESS is not a valid Ethereum address.")
        return w3.eth.contract(
            address=Web3.to_checksum_address(self.contract_address), abi=self.abi
        )

    def _recorder_account(self):
        if not self.recorder_key:
            raise ChainError(
                "RECORDER_PRIVATE_KEY is not configured. Add the deployed owner wallet key to .env."
            )
        key = self.recorder_key if self.recorder_key.startswith("0x") else "0x" + self.recorder_key
        try:
            return Account.from_key(key)
        except Exception as exc:
            raise ChainError(f"Invalid RECORDER_PRIVATE_KEY: {exc}") from exc

    # ----------------------------------------------------------------- status
    def status(self, force: bool = False) -> dict:
        now = time.time()
        if not force and self._status_cache and now - self._status_cached_at < 20:
            return self._status_cache

        base = {
            "mode": self.mode,
            "chain_name": "Ethereum Sepolia" if not self.is_simulation else "Simulated proof ledger",
            "chain_id": self.config["SEPOLIA_CHAIN_ID"] if self.config else 11155111,
            "contract_address": self.contract_address,
            "explorer": self.config["EXPLORER_BASE_URL"] if self.config else "",
            "recorder_address": self.recorder_address,
            "connected": False,
            "configured": self.is_configured,
            "block_number": None,
            "record_count": None,
            "balance_eth": None,
            "error": None,
        }
        try:
            if self.is_simulation:
                row = db.fetch_one("SELECT COUNT(*) AS c, COALESCE(MAX(block_number),0) AS b FROM sim_blocks")
                base.update(
                    connected=True,
                    record_count=int(row["c"]) if row else 0,
                    block_number=int(row["b"]) if row else 0,
                    contract_address="simulated://aqualedger",
                    explorer="",
                )
            else:
                if not self.is_configured:
                    raise ChainError(
                        "Sepolia mode is missing CONTRACT_ADDRESS or RECORDER_PRIVATE_KEY."
                    )
                w3 = self._web3()
                contract = self._contract()
                base.update(
                    connected=True,
                    block_number=w3.eth.block_number,
                    record_count=contract.functions.recordCount().call(),
                    balance_eth=round(float(w3.from_wei(w3.eth.get_balance(self.recorder_address), "ether")), 5),
                )
        except Exception as exc:
            base["error"] = str(exc)
        self._status_cache = base
        self._status_cached_at = now
        return base

    def is_recorder(self, address: str) -> bool:
        if not address:
            return False
        if self.is_simulation:
            return True
        try:
            return bool(self._contract().functions.isRecorder(Web3.to_checksum_address(address)).call())
        except Exception:
            return False

    def owner_address(self) -> str | None:
        if self.is_simulation:
            return "simulated"
        try:
            return self._contract().functions.owner().call()
        except Exception:
            return None

    # ---------------------------------------------------------------- wallet
    def verify_wallet_signature(self, address: str, message: str, signature: str) -> bool:
        try:
            recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
            return recovered.lower() == address.lower()
        except Exception:
            return False

    # ---------------------------------------------------------------- anchor
    def submit_anchor(self, reading_id: int) -> dict:
        """Queue (or execute) anchoring for a reading. Returns immediately."""
        record = self._load_record(reading_id)
        if not record:
            raise ChainError("Reading not found")
        if record["status"] == "anchored":
            return {"queued": False, "reason": "already anchored"}
        if not self.is_configured:
            raise ChainError("Blockchain is not configured")

        with _active_lock:
            if reading_id in _active_anchors:
                return {"queued": True, "reason": "already in progress"}
            _active_anchors.add(reading_id)

        tx_id = db.execute(
            """INSERT INTO blockchain_transactions
               (reading_id, household_id, chain_mode, contract_address, from_address, record_hash,
                status, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,'pending',%s)""",
            (
                reading_id,
                record["household_id"],
                "simulation" if self.is_simulation else "sepolia",
                "simulated://aqualedger" if self.is_simulation else self.contract_address,
                "simulation" if self.is_simulation else self.recorder_address,
                record["record_hash"],
                utc_now(),
            ),
        )
        db.execute(
            "UPDATE readings SET status='pending', chain_mode=%s WHERE id=%s",
            ("simulation" if self.is_simulation else "sepolia", reading_id),
        )

        try:
            if self.is_simulation:
                self._anchor_simulation(record, tx_id)
            else:
                threading.Thread(
                    target=self._anchor_sepolia_worker,
                    args=(reading_id, tx_id),
                    daemon=True,
                    name=f"anchor-{reading_id}",
                ).start()
        finally:
            if self.is_simulation:
                with _active_lock:
                    _active_anchors.discard(reading_id)
        return {"queued": True, "transaction_id": tx_id, "mode": self.mode}

    def _load_record(self, reading_id: int) -> dict | None:
        return db.fetch_one(
            """SELECT r.*, h.chain_ref, h.meter_id, h.members_count, h.household_code,
                      h.name AS household_name
               FROM readings r JOIN households h ON h.id = r.household_id
               WHERE r.id = %s""",
            (reading_id,),
        )

    def _anchor_simulation(self, record: dict, tx_id: int) -> dict:
        existing = db.fetch_one("SELECT * FROM sim_blocks WHERE reading_id=%s", (record["id"],))
        if existing:
            self._mark_anchored(record["id"], tx_id, existing["tx_hash"], existing["block_number"], existing["created_at"], "simulation")
            return existing

        last = db.fetch_one("SELECT record_hash, block_number FROM sim_blocks ORDER BY block_number DESC LIMIT 1")
        prev_hash = last["record_hash"] if last else ZERO_HASH
        created = utc_now()
        seed = f"{prev_hash}|{record['record_hash']}|{record['id']}|{iso_utc(created)}"
        tx_hash = "0x" + Web3.keccak(text=seed).hex()

        block_number = db.execute(
            """INSERT INTO sim_blocks (prev_hash, record_hash, reading_id, tx_hash, created_at)
               VALUES (%s,%s,%s,%s,%s)""",
            (prev_hash, record["record_hash"], record["id"], tx_hash, created),
        )
        self._mark_anchored(record["id"], tx_id, tx_hash, block_number, created, "simulation")
        logger.info("Simulated anchor reading=%s block=%s", record["id"], block_number)
        return {"tx_hash": tx_hash, "block_number": block_number, "created_at": created}

    def _anchor_sepolia_worker(self, reading_id: int, tx_id: int) -> None:
        try:
            record = self._load_record(reading_id)
            if not record:
                raise ChainError("Reading disappeared before anchoring")
            contract = self._contract()
            w3 = self._web3()
            account = self._recorder_account()

            # Guard against re-anchoring after a success in another process.
            if record["status"] == "anchored" and record["chain_tx_hash"]:
                self._close_transaction(tx_id, "confirmed", tx_hash=record["chain_tx_hash"], block=record["chain_block"])
                return

            contract.functions.isRecorder(account.address).call()  # connectivity + permission check
            if not contract.functions.isRecorder(account.address).call():
                raise ChainError(
                    f"Recorder wallet {account.address} is not authorised on the contract. "
                    "Call setRecorder from the contract owner."
                )

            gas_price = w3.eth.gas_price
            max_gas = int(Web3.to_wei(self.config["MAX_GAS_PRICE_GWEI"], "gwei"))
            if gas_price > max_gas:
                raise ChainError(
                    f"Gas price {round(float(w3.from_wei(gas_price, 'gwei')), 2)} gwei exceeds the configured cap "
                    f"of {self.config['MAX_GAS_PRICE_GWEI']} gwei."
                )

            fn = contract.functions.anchorRecord(
                bytes.fromhex(record["chain_ref"][2:]),
                int(record["sequence"]),
                int(round(float(record["consumption_l"]) * 1000)),
                int(to_unix_date(record["reading_date"])),
                bytes.fromhex(record["record_hash"][2:]),
            )
            tx = fn.build_transaction(
                {
                    "from": account.address,
                    "nonce": w3.eth.get_transaction_count(account.address, "pending"),
                    "gas": int(fn.estimate_gas({"from": account.address}) * 1.25),
                    "gasPrice": min(gas_price, max_gas),
                    "chainId": self.config["SEPOLIA_CHAIN_ID"],
                }
            )
            signed = Account.sign_transaction(tx, account.key)
            raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
            submitted = w3.eth.send_raw_transaction(raw)
            tx_hash = submitted.to_0x_hex()
            db.execute(
                "UPDATE blockchain_transactions SET status='submitted', tx_hash=%s WHERE id=%s",
                (tx_hash, tx_id),
            )
            logger.info("Sepolia tx submitted %s for reading %s", tx_hash, reading_id)

            receipt = w3.eth.wait_for_transaction_receipt(
                submitted, timeout=self.config["ANCHOR_TIMEOUT_SECONDS"], poll_latency=4
            )
            if receipt.status != 1:
                raise ChainError(f"Transaction {tx_hash} reverted on-chain")

            event = self._find_anchor_event(receipt, record)
            if event is None:
                raise ChainError("Transaction confirmed but the expected RecordAnchored event was not found")
            if event["args"]["recordHash"].hex().lower().replace("0x", "") != record["record_hash"].lower().replace("0x", ""):
                raise ChainError("On-chain record hash does not match the submitted record")

            chain_timestamp = datetime.fromtimestamp(
                receipt["blockNumber"] and w3.eth.get_block(receipt["blockNumber"])["timestamp"] or time.time(),
                tz=timezone.utc,
            ).replace(tzinfo=None)
            self._mark_anchored(
                reading_id,
                tx_id,
                tx_hash,
                receipt["blockNumber"],
                chain_timestamp,
                "sepolia",
                gas_used=receipt["gasUsed"],
                anchored_by=account.address,
            )
            logger.info("Sepolia anchor confirmed reading=%s tx=%s block=%s", reading_id, tx_hash, receipt["blockNumber"])
        except Exception as exc:
            logger.exception("Sepolia anchoring failed for reading %s", reading_id)
            self._mark_failed(reading_id, tx_id, str(exc))
        finally:
            with _active_lock:
                _active_anchors.discard(reading_id)

    def _find_anchor_event(self, receipt, record: dict):
        contract = self._contract()
        try:
            events = contract.events.RecordAnchored().process_receipt(receipt)
        except Exception:
            events = []
        for event in events:
            args = event["args"]
            if (
                args["householdRef"].hex().lstrip("0").lower()
                == record["chain_ref"][2:].lstrip("0").lower()
                and int(args["sequence"]) == int(record["sequence"])
            ):
                return event
        return None

    def _mark_anchored(
        self,
        reading_id: int,
        tx_id: int,
        tx_hash: str,
        block_number: int,
        chain_timestamp,
        mode: str,
        gas_used: int | None = None,
        anchored_by: str | None = None,
    ) -> None:
        db.execute(
            """UPDATE readings SET status='anchored', chain_mode=%s, chain_tx_hash=%s, chain_block=%s,
                   chain_timestamp=%s, chain_confirmations=1, anchored_by=%s
               WHERE id=%s""",
            (mode, tx_hash, block_number, chain_timestamp, anchored_by or "simulation", reading_id),
        )
        db.execute(
            """UPDATE blockchain_transactions SET status='confirmed', tx_hash=%s, block_number=%s,
                   gas_used=%s, confirmed_at=%s WHERE id=%s""",
            (tx_hash, block_number, gas_used, utc_now(), tx_id),
        )

    def _close_transaction(self, tx_id: int, status: str, tx_hash: str | None = None, block: int | None = None) -> None:
        db.execute(
            "UPDATE blockchain_transactions SET status=%s, tx_hash=COALESCE(%s, tx_hash), block_number=COALESCE(%s, block_number), confirmed_at=%s WHERE id=%s",
            (status, tx_hash, block, utc_now(), tx_id),
        )

    def _mark_failed(self, reading_id: int, tx_id: int, message: str) -> None:
        message = (message or "Unknown blockchain error")[:2000]
        db.execute("UPDATE readings SET status='failed' WHERE id=%s", (reading_id,))
        db.execute(
            "UPDATE blockchain_transactions SET status='failed', error_message=%s WHERE id=%s",
            (message, tx_id),
        )
        try:
            record = db.fetch_one("SELECT household_id FROM readings WHERE id=%s", (reading_id,))
            if record:
                db.execute(
                    """INSERT INTO alerts (household_id, reading_id, alert_type, severity, title, message, created_at)
                       VALUES (%s,%s,'blockchain','warning','Blockchain anchoring failed',%s,%s)""",
                    (
                        record["household_id"],
                        reading_id,
                        "The reading is safely stored in MySQL but its proof could not be anchored on the configured chain. "
                        "An administrator can retry anchoring from the Blockchain page. Details: " + message[:400],
                        utc_now(),
                    ),
                )
        except Exception:
            logger.exception("Could not create blockchain failure alert")

    # ---------------------------------------------------------------- verify
    def verify_record(self, reading: dict, household: dict, current_hash: str) -> dict:
        """Compare the off-chain record with its on-chain proof."""
        result = {
            "mode": "simulation" if self.is_simulation else "sepolia",
            "exists": False,
            "hash_matches": False,
            "consumption_matches": False,
            "date_matches": False,
            "on_chain_hash": None,
            "anchored_by": None,
            "anchored_at": None,
            "block_number": None,
            "tx_hash": reading.get("chain_tx_hash"),
            "error": None,
        }
        try:
            if self.is_simulation:
                block = db.fetch_one("SELECT * FROM sim_blocks WHERE reading_id=%s", (reading["id"],))
                if not block:
                    result["error"] = "No simulated proof found for this reading."
                    return result
                result.update(
                    exists=True,
                    on_chain_hash=block["record_hash"],
                    hash_matches=block["record_hash"].lower() == current_hash.lower(),
                    consumption_matches=True,
                    date_matches=True,
                    anchored_by="simulation",
                    anchored_at=block["created_at"],
                    block_number=block["block_number"],
                    tx_hash=block["tx_hash"],
                )
                return result

            contract = self._contract()
            detailed = contract.functions.verifyRecordDetailed(
                bytes.fromhex(household["chain_ref"][2:]),
                int(reading["sequence"]),
                bytes.fromhex(current_hash[2:]),
            ).call()
            exists, hash_matches, consumption_ml, reading_ts, anchored_by, anchored_at = detailed
            on_chain_hash = None
            if exists:
                stored = contract.functions.getRecord(
                    bytes.fromhex(household["chain_ref"][2:]),
                    int(reading["sequence"]),
                ).call()
                on_chain_hash = "0x" + stored[4].hex()
            from datetime import date

            expected_date = to_unix_date(reading["reading_date"])
            result.update(
                exists=bool(exists),
                hash_matches=bool(hash_matches),
                consumption_matches=bool(exists) and int(consumption_ml) == int(round(float(reading["consumption_l"]) * 1000)),
                date_matches=bool(exists) and int(reading_ts) == int(expected_date),
                on_chain_hash=on_chain_hash,
                anchored_by=anchored_by,
                anchored_at=datetime.fromtimestamp(int(anchored_at), tz=timezone.utc).replace(tzinfo=None) if anchored_at else None,
                block_number=reading.get("chain_block"),
            )
        except Exception as exc:
            result["error"] = str(exc)
        return result

    # ------------------------------------------------------- MetaMask receipt
    def confirm_metamask_tx(self, reading: dict, household: dict, tx_hash: str, from_address: str) -> dict:
        """Validate a MetaMask-signed anchoring transaction and persist it."""
        if self.is_simulation:
            raise ChainError("MetaMask anchoring requires BLOCKCHAIN_MODE=sepolia")
        w3 = self._web3()
        if not Web3.is_hex(tx_hash) or len(tx_hash) != 66:
            raise ChainError("Invalid transaction hash")
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
        except Exception:
            return {"status": "pending", "message": "Transaction is not mined yet. Try again in a few seconds."}
        if receipt is None:
            return {"status": "pending", "message": "Transaction is not mined yet. Try again in a few seconds."}
        if receipt.status != 1:
            raise ChainError("The transaction reverted on-chain.")
        if from_address and receipt["from"].lower() != from_address.lower():
            raise ChainError("Transaction sender does not match your linked wallet.")

        event = self._find_anchor_event(receipt, reading)
        if event is None:
            raise ChainError(
                "The transaction does not contain a matching RecordAnchored event for this household and sequence."
            )
        onchain_hash = "0x" + event["args"]["recordHash"].hex()
        if onchain_hash.lower() != reading["record_hash"].lower():
            raise ChainError("The event record hash does not match the off-chain record hash.")

        existing_tx = db.fetch_one("SELECT id FROM readings WHERE chain_tx_hash=%s AND id<>%s", (tx_hash, reading["id"]))
        if existing_tx:
            raise ChainError("This transaction has already been used for another reading.")

        block = w3.eth.get_block(receipt["blockNumber"])
        chain_ts = datetime.fromtimestamp(block["timestamp"], tz=timezone.utc).replace(tzinfo=None)
        tx_id = db.execute(
            """INSERT INTO blockchain_transactions
               (reading_id, household_id, chain_mode, contract_address, tx_hash, from_address,
                record_hash, status, block_number, gas_used, created_at, confirmed_at)
               VALUES (%s,%s,'sepolia',%s,%s,%s,%s,'confirmed',%s,%s,%s,%s)""",
            (
                reading["id"],
                reading["household_id"],
                self.contract_address,
                tx_hash,
                receipt["from"],
                reading["record_hash"],
                receipt["blockNumber"],
                receipt["gasUsed"],
                utc_now(),
                utc_now(),
            ),
        )
        db.execute(
            """UPDATE readings SET status='anchored', chain_mode='sepolia', chain_tx_hash=%s,
                   chain_block=%s, chain_timestamp=%s, chain_confirmations=1, anchored_by=%s
               WHERE id=%s""",
            (tx_hash, receipt["blockNumber"], chain_ts, receipt["from"], reading["id"]),
        )
        return {
            "status": "confirmed",
            "tx_hash": tx_hash,
            "block_number": receipt["blockNumber"],
            "chain_timestamp": iso_utc(chain_ts),
            "gas_used": receipt["gasUsed"],
            "transaction_id": tx_id,
        }

    # --------------------------------------------------------- recorder admin
    def set_recorder(self, wallet: str, allowed: bool) -> dict:
        if self.is_simulation:
            return {"tx_hash": "0x" + "ab" * 32, "mode": "simulation", "status": "confirmed"}
        if not Web3.is_address(wallet):
            raise ChainError("Invalid wallet address")
        contract = self._contract()
        w3 = self._web3()
        account = self._recorder_account()
        if contract.functions.owner().call().lower() != account.address.lower():
            raise ChainError(
                "The configured RECORDER_PRIVATE_KEY is not the contract owner, so it cannot authorise wallets. "
                "Run setRecorder from the owner wallet in Remix, or redeploy using the recorder wallet as owner."
            )
        fn = contract.functions.setRecorder(Web3.to_checksum_address(wallet), allowed)
        tx = fn.build_transaction(
            {
                "from": account.address,
                "nonce": w3.eth.get_transaction_count(account.address, "pending"),
                "gas": int(fn.estimate_gas({"from": account.address}) * 1.2),
                "gasPrice": w3.eth.gas_price,
                "chainId": self.config["SEPOLIA_CHAIN_ID"],
            }
        )
        signed = Account.sign_transaction(tx, account.key)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        submitted = w3.eth.send_raw_transaction(raw)
        tx_hash = submitted.to_0x_hex()
        receipt = w3.eth.wait_for_transaction_receipt(
            submitted, timeout=self.config["ANCHOR_TIMEOUT_SECONDS"], poll_latency=3
        )
        if receipt.status != 1:
            raise ChainError("setRecorder transaction reverted")
        return {"tx_hash": tx_hash, "mode": "sepolia", "status": "confirmed", "block_number": receipt["blockNumber"]}

    # -------------------------------------------------------- sim integrity
    def verify_simulation_chain(self) -> dict:
        blocks = db.fetch_all(
            """SELECT b.*, r.record_hash AS reading_hash, r.consumption_l, r.status AS reading_status
               FROM sim_blocks b JOIN readings r ON r.id = b.reading_id
               ORDER BY b.block_number ASC"""
        )
        issues = []
        expected_prev = ZERO_HASH
        for block in blocks:
            if block["prev_hash"].lower() != expected_prev.lower():
                issues.append(
                    {
                        "block": block["block_number"],
                        "reading_id": block["reading_id"],
                        "issue": "Broken hash-chain link",
                    }
                )
            seed = f"{block['prev_hash']}|{block['record_hash']}|{block['reading_id']}|{iso_utc(block['created_at'])}"
            expected_tx = "0x" + Web3.keccak(text=seed).hex()
            if block["tx_hash"].lower() != expected_tx.lower():
                issues.append(
                    {
                        "block": block["block_number"],
                        "reading_id": block["reading_id"],
                        "issue": "Invalid simulated transaction digest",
                    }
                )
            if block["record_hash"].lower() != block["reading_hash"].lower():
                issues.append(
                    {
                        "block": block["block_number"],
                        "reading_id": block["reading_id"],
                        "issue": "Current record digest no longer matches anchored digest",
                    }
                )
            expected_prev = block["record_hash"]
        return {
            "mode": "simulation",
            "blocks": len(blocks),
            "valid": not issues,
            "issues": issues,
        }


chain = ChainService()


def anchor_message(address: str, nonce: str) -> str:
    return (
        "AquaLedger wallet verification\n\n"
        "Sign this message to prove you own this wallet. This signature is free and does not "
        "create a blockchain transaction.\n\n"
        f"Wallet: {address.lower()}\nNonce: {nonce}\nPurpose: link wallet to AquaLedger account"
    )
