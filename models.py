"""User model backed by MySQL and Flask-Login user loader."""
from __future__ import annotations

from flask_login import UserMixin

from . import db


class User(UserMixin):
    def __init__(self, row: dict):
        self._row = row
        self.id = int(row["id"])
        self.name = row["name"]
        self.email = row["email"]
        self.role = row["role"]
        self.phone = row.get("phone")
        self.wallet_address = row.get("wallet_address")
        self.wallet_verified = bool(row.get("wallet_verified"))
        self.is_active_flag = bool(row.get("is_active", 1))
        self.last_login = row.get("last_login")
        self.created_at = row.get("created_at")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_active(self) -> bool:  # Flask-Login hook
        return self.is_active_flag

    @property
    def initials(self) -> str:
        parts = [part for part in (self.name or "").split() if part]
        return "".join(part[0].upper() for part in parts[:2]) or "AL"

    @property
    def first_name(self) -> str:
        return (self.name or "there").split(" ")[0]

    @classmethod
    def get(cls, user_id):
        try:
            row = db.fetch_one("SELECT * FROM users WHERE id=%s", (int(user_id),))
        except Exception:
            return None
        return cls(row) if row else None

    @classmethod
    def by_email(cls, email: str):
        row = db.fetch_one("SELECT * FROM users WHERE email=%s", (email.strip().lower(),))
        return cls(row) if row else None

    def refresh(self) -> "User":
        row = db.fetch_one("SELECT * FROM users WHERE id=%s", (self.id,))
        if row:
            self.__init__(row)
        return self

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "wallet_address": self.wallet_address,
            "wallet_verified": self.wallet_verified,
        }


def get_user(user_id: int) -> User | None:
    return User.get(user_id)
