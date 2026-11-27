"""Refresh-token primitives (ADR-006, ADR-022).

A refresh token is an **opaque** random string: 256 bits from the OS CSPRNG, nothing
readable inside. Only its SHA-256 digest is stored.

Why SHA-256 and not Argon2 like passwords? Password hashing must be slow because humans pick
guessable passwords. A 256-bit random token cannot be guessed, so a fast, deterministic hash
is enough, and determinism lets us find the row with a unique-index lookup. A leaked
database still yields no usable tokens.
"""

import hashlib
import secrets

from app.core.errors import AuthenticationError

TOKEN_BYTES = 32  # 256 bits of entropy


class InvalidRefreshTokenError(AuthenticationError):
    """Unknown, revoked, already-used (reuse) or otherwise unusable refresh token.

    Reuse is deliberately reported the same way as "unknown": the response must not tell an
    attacker that their stolen token was detected.
    """

    default_code = "INVALID_REFRESH_TOKEN"
    default_message = "Your session is no longer valid. Please sign in again."


class RefreshTokenExpiredError(AuthenticationError):
    default_code = "REFRESH_TOKEN_EXPIRED"
    default_message = "Your session has expired. Please sign in again."


def generate_refresh_token() -> str:
    """43 URL-safe characters (256 random bits)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """64 hex characters; what the database stores and looks up."""
    return hashlib.sha256(token.encode()).hexdigest()
