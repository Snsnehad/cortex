"""Refresh tokens (ADR-006): opaque, stored hashed, rotated on every use.

All rotations that descend from one login share a ``family_id``. Presenting a token that has
already been replaced means it was stolen (or replayed), so the whole family is revoked.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CHAR, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User

TOKEN_HASH_LENGTH = 64  # SHA-256 hex digest


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index(None, "user_id"),
        Index(None, "family_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    family_id: Mapped[uuid.UUID]
    # SHA-256 of the raw token. The raw token is never stored: a database leak must not hand
    # out working sessions. Unique, so a lookup by hash is one index probe.
    token_hash: Mapped[str] = mapped_column(CHAR(TOKEN_HASH_LENGTH), unique=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )
    user_agent: Mapped[str | None] = mapped_column(String(300))

    user: Mapped["User"] = relationship(back_populates="refresh_tokens", lazy="raise")

    def __repr__(self) -> str:
        # Never include token_hash.
        return f"<RefreshToken id={self.id} family={self.family_id} revoked={self.revoked_at}>"
