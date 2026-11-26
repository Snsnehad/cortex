"""Password hashing (Argon2id) and the password policy.

Hashing
-------
* Argon2id with OWASP's recommended parameters: m = 19 MiB, t = 2, p = 1 (OWASP Password
  Storage Cheat Sheet). The library default (64 MiB, t=3, p=4) is stronger per hash, but 20
  concurrent logins would need ~1.3 GB of RAM; at 19 MiB they need ~380 MB.
* The encoded hash stores algorithm, parameters and salt (``$argon2id$v=19$m=19456,t=2,p=1$…``),
  so parameters can change later: ``verify`` reports when a stored hash uses outdated
  parameters and returns a fresh hash for the caller to save (transparent upgrade on login).
* Hashing costs tens of milliseconds of CPU. Async code must use ``hash_async`` /
  ``verify_async``, which run in a worker thread so the event loop keeps serving other
  requests (ARCHITECTURE.md §5).
* Passwords are NFKC-normalised before hashing, so the same password typed on different
  keyboards or input methods matches (NIST SP 800-63B §5.1.1.2).

Policy (NIST SP 800-63B, not composition rules)
------------------------------------------------
Length 12 to 128 characters; not only whitespace; not the account's email or its local part.
No "must contain a digit/symbol" rules: NIST advises against them because they push users
towards predictable patterns. The maximum bounds the work an attacker can force per request.
"""

import unicodedata
from dataclasses import dataclass
from typing import Any

import anyio
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.errors import ValidationFailedError

ARGON2_TIME_COST = 2
ARGON2_MEMORY_COST_KIB = 19 * 1024  # 19 MiB
ARGON2_PARALLELISM = 1

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128


@dataclass(frozen=True, slots=True)
class VerifyResult:
    valid: bool
    # A new hash to store when the password was correct but the stored hash used outdated
    # parameters; None otherwise.
    new_hash: str | None = None


def _normalize(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


class PasswordHasher:
    def __init__(
        self,
        *,
        time_cost: int = ARGON2_TIME_COST,
        memory_cost_kib: int = ARGON2_MEMORY_COST_KIB,
        parallelism: int = ARGON2_PARALLELISM,
    ) -> None:
        self._hasher = PasswordHash(
            (
                Argon2Hasher(
                    time_cost=time_cost, memory_cost=memory_cost_kib, parallelism=parallelism
                ),
            )
        )
        self._dummy_hash: str | None = None

    def hash(self, password: str) -> str:
        return self._hasher.hash(_normalize(password))

    def verify(self, password: str, stored_hash: str) -> VerifyResult:
        try:
            valid, updated = self._hasher.verify_and_update(_normalize(password), stored_hash)
        except UnknownHashError:
            # Corrupt or foreign hash in the database: treat as a failed login, never a 500.
            return VerifyResult(valid=False)
        return VerifyResult(valid=valid, new_hash=updated if valid else None)

    def dummy_verify(self, password: str) -> None:
        """Spend the same time as a real verification, for logins with an unknown email.

        Without it, "no such user" answers measurably faster than "wrong password", which
        lets an attacker discover which emails have accounts (account enumeration).
        """
        if self._dummy_hash is None:
            self._dummy_hash = self.hash("dummy-password-for-constant-time-login")
        self._hasher.verify(_normalize(password), self._dummy_hash)

    async def hash_async(self, password: str) -> str:
        return await anyio.to_thread.run_sync(self.hash, password)

    async def verify_async(self, password: str, stored_hash: str) -> VerifyResult:
        return await anyio.to_thread.run_sync(self.verify, password, stored_hash)

    async def dummy_verify_async(self, password: str) -> None:
        await anyio.to_thread.run_sync(self.dummy_verify, password)


def password_policy_violations(password: str, *, email: str | None = None) -> list[dict[str, Any]]:
    """All policy violations, as field errors in the API's 422 format (empty list = valid)."""
    normalized = _normalize(password)
    violations: list[dict[str, Any]] = []

    def violation(type_: str, msg: str) -> None:
        violations.append({"loc": ["body", "password"], "msg": msg, "type": type_})

    if len(normalized) < PASSWORD_MIN_LENGTH:
        violation(
            "password_too_short", f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
        )
    if len(normalized) > PASSWORD_MAX_LENGTH:
        violation(
            "password_too_long", f"Password must be at most {PASSWORD_MAX_LENGTH} characters."
        )
    if normalized and not normalized.strip():
        violation("password_blank", "Password cannot consist only of whitespace.")
    if email:
        lowered = normalized.casefold()
        address = _normalize(email).casefold()
        local_part = address.split("@", 1)[0]
        if lowered in {address, local_part}:
            violation("password_matches_email", "Password must not be your email address.")
    return violations


def validate_password(password: str, *, email: str | None = None) -> None:
    """Raise ValidationFailedError (422) listing every violation of the password policy."""
    violations = password_policy_violations(password, email=email)
    if violations:
        raise ValidationFailedError(
            "The password does not meet the requirements.",
            code="PASSWORD_POLICY",
            details={"fields": violations},
        )
