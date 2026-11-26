"""Access tokens: short-lived signed JWTs (ADR-006).

A client sends ``Authorization: Bearer <token>`` on every request. The token proves *who* the
caller is, for at most ``auth.access_token_ttl_seconds`` (15 minutes by default). What the
caller may *do* is decided per request from the database (organization memberships), never
from the token, so role changes take effect immediately.

Hardening:

* **Algorithm pinned** to HS256 on verification. ``alg: none`` and every other algorithm are
  rejected, even when correctly signed.
* **Every claim is required**: ``sub, iat, exp, jti, typ, iss, aud``. ``typ`` must be
  ``access``, so no other token we may issue later can be replayed as an access token;
  ``iss``/``aud`` pin the token to this service.
* **Dedicated signing key** derived from the configured secret (as for pagination cursors),
  so no key is used for two purposes.
* **Minimal content**: only the user id. JWTs are signed, not encrypted: anyone holding one
  can read it, so no email, name or role goes in.
* Two failure codes: ``TOKEN_EXPIRED`` (client should refresh) and ``INVALID_TOKEN`` (client
  must log in again). Both are 401.
"""

import hashlib
import hmac
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.errors import AuthenticationError

ALGORITHM = "HS256"
ISSUER = "cortex"
AUDIENCE = "cortex-api"
TOKEN_TYPE_ACCESS = "access"  # noqa: S105  (a claim value, not a credential)
CLOCK_SKEW_LEEWAY = timedelta(seconds=30)
_KEY_CONTEXT = b"cortex/jwt-access-token/v1"
_REQUIRED_CLAIMS = ["sub", "iat", "exp", "jti", "typ", "iss", "aud"]


class TokenExpiredError(AuthenticationError):
    default_code = "TOKEN_EXPIRED"
    default_message = "The access token has expired. Refresh it and retry."


class InvalidTokenError(AuthenticationError):
    default_code = "INVALID_TOKEN"
    default_message = "The access token is invalid."


@dataclass(frozen=True, slots=True)
class AccessToken:
    token: str
    expires_at: datetime
    expires_in: int  # seconds, for the login/refresh response


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    token_id: str
    issued_at: datetime
    expires_at: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AccessTokenService:
    def __init__(
        self,
        secret: str,
        *,
        ttl_seconds: int,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if len(secret) < 16:
            raise ValueError("JWT secret too short")
        self._key = hmac.new(secret.encode(), _KEY_CONTEXT, hashlib.sha256).digest()
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock

    def __repr__(self) -> str:
        return f"AccessTokenService(ttl={self._ttl}, alg={ALGORITHM})"  # never the key

    def issue(self, user_id: uuid.UUID) -> AccessToken:
        issued_at = self._clock().replace(microsecond=0)
        expires_at = issued_at + self._ttl
        payload = {
            "sub": str(user_id),
            "iat": issued_at,
            "exp": expires_at,
            "jti": uuid.uuid4().hex,
            "typ": TOKEN_TYPE_ACCESS,
            "iss": ISSUER,
            "aud": AUDIENCE,
        }
        token = jwt.encode(payload, self._key, algorithm=ALGORITHM)
        return AccessToken(
            token=token, expires_at=expires_at, expires_in=int(self._ttl.total_seconds())
        )

    def verify(self, token: str) -> AccessTokenClaims:
        """Return the claims, or raise TokenExpiredError / InvalidTokenError (both 401)."""
        try:
            claims = jwt.decode(
                token,
                self._key,
                algorithms=[ALGORITHM],  # pinned: never trust the token's own header
                audience=AUDIENCE,
                issuer=ISSUER,
                leeway=CLOCK_SKEW_LEEWAY,
                options={"require": _REQUIRED_CLAIMS},
            )
            if claims.get("typ") != TOKEN_TYPE_ACCESS:
                raise InvalidTokenError
            return AccessTokenClaims(
                user_id=uuid.UUID(str(claims["sub"])),
                token_id=str(claims["jti"]),
                issued_at=datetime.fromtimestamp(int(claims["iat"]), UTC),
                expires_at=datetime.fromtimestamp(int(claims["exp"]), UTC),
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError from exc
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
            # Any other problem (signature, algorithm, claims, malformed): one answer, so the
            # response reveals nothing about *why* a forged token failed.
            raise InvalidTokenError from exc
