"""User accounts (DATABASE_DESIGN.md: users)."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Index, String, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.organization import OrganizationMembership
    from app.models.refresh_token import RefreshToken

EMAIL_MAX_LENGTH = 320  # RFC 5321: 64 (local) + 1 (@) + 255 (domain)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH))
    # Argon2id encoded hash (algorithm, parameters, salt and hash in one string).
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(server_default=true())
    last_login_at: Mapped[datetime | None]

    # lazy="raise": under asyncio an implicit lazy load is an error (or a hidden N+1 query).
    # Relationships must be loaded explicitly, e.g. selectinload(User.memberships).
    memberships: Mapped[list["OrganizationMembership"]] = relationship(
        back_populates="user", lazy="raise", passive_deletes=True
    )
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", lazy="raise", passive_deletes=True
    )

    def __repr__(self) -> str:
        # Deliberately excludes email and password_hash: reprs end up in logs and tracebacks.
        return f"<User id={self.id}>"


# Case-insensitive uniqueness: Alice@Example.com and alice@example.com are the same account.
# The address is stored as entered; lookups compare lower(email).
# Defined after the class so it references the real column. func.lower("email") would
# lower-case the string literal 'email', a constant that allows exactly one user.
# Explicit name: an expression index has no column name for the naming convention.
Index("uq_users_email_lower", func.lower(User.email), unique=True)
