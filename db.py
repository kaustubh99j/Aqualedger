"""MySQL connection pooling and small query helpers.

All queries in AquaLedger use parameter binding. The helpers return plain
dictionaries so templates and JSON APIs can consume rows directly.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterable, Sequence

import mysql.connector
from mysql.connector import Error, pooling

logger = logging.getLogger(__name__)

_pool: pooling.MySQLConnectionPool | None = None
_pool_error: str | None = None


def init_pool(app) -> None:
    """Create the global connection pool. Safe to call once per process."""
    global _pool, _pool_error
    if _pool is not None:
        return
    try:
        _pool = pooling.MySQLConnectionPool(
            pool_name="aqualedger_pool",
            pool_size=app.config["DB_POOL_SIZE"],
            pool_reset_session=True,
            host=app.config["DB_HOST"],
            port=app.config["DB_PORT"],
            user=app.config["DB_USER"],
            password=app.config["DB_PASSWORD"],
            database=app.config["DB_NAME"],
            charset="utf8mb4",
            use_pure=True,
            autocommit=False,
            connection_timeout=10,
        )
        # Normalise the session timezone so hashed timestamps are reproducible.
        conn = _pool.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SET time_zone = '+00:00'")
            cur.close()
            conn.commit()
        finally:
            conn.close()
        _pool_error = None
        logger.info("MySQL pool ready on %s:%s/%s", app.config["DB_HOST"], app.config["DB_PORT"], app.config["DB_NAME"])
    except Error as exc:
        _pool = None
        _pool_error = str(exc)
        logger.error("MySQL pool initialisation failed: %s", exc)


def pool_error() -> str | None:
    return _pool_error


@contextmanager
def get_connection():
    """Yield a pooled connection, rolling back on error and closing safely."""
    if _pool is None:
        raise RuntimeError("Database pool is not initialised. Run scripts/init_db.py and restart the app.")
    conn = _pool.get_connection()
    try:
        yield conn
    except Exception:
        try:
            conn.rollback()
        except Error:
            pass
        raise
    finally:
        try:
            conn.close()
        except Error:
            pass


@contextmanager
def transaction():
    """Context manager yielding a dictionary cursor inside a transaction."""
    with get_connection() as conn:
        cur = conn.cursor(dictionary=True)
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def fetch_all(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    with get_connection() as conn:
        cur = conn.cursor(dictionary=True)
        try:
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            return list(cur.fetchall())
        finally:
            cur.close()


def fetch_one(sql: str, params: Sequence[Any] = ()) -> dict | None:
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def fetch_value(sql: str, params: Sequence[Any] = (), default=None):
    row = fetch_one(sql, params)
    if not row:
        return default
    return next(iter(row.values()))


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    """Execute a statement and return lastrowid (insert) or rowcount (update)."""
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            conn.commit()
            return cur.lastrowid if cur.lastrowid else cur.rowcount
        finally:
            cur.close()


def execute_many(sql: str, params: Iterable[Sequence[Any]]) -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.executemany(sql, list(params))
            conn.commit()
            return cur.rowcount
        finally:
            cur.close()
