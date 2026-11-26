"""Password hashing and password policy (TASK-03.01)."""

import asyncio
import statistics
import time

import pytest

from app.core.errors import ValidationFailedError
from app.core.passwords import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    PasswordHasher,
    password_policy_violations,
    validate_password,
)
from app.schemas.errors import FieldError

hasher = PasswordHasher()
PASSWORD = "correct horse battery staple"


def to_fullwidth(text: str) -> str:
    """ASCII letters -> FULLWIDTH forms (U+FF01..U+FF5E), e.g. typed with an East Asian IME.

    Generated rather than written literally: look-alike characters in source code are hard
    to review (ruff RUF001).
    """
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)


# --- hashing -----------------------------------------------------------------------------------


def test_hash_is_argon2id_with_the_configured_parameters() -> None:
    assert hasher.hash(PASSWORD).startswith("$argon2id$v=19$m=19456,t=2,p=1$")


def test_hashes_are_salted() -> None:
    first, second = hasher.hash(PASSWORD), hasher.hash(PASSWORD)

    assert first != second
    assert hasher.verify(PASSWORD, first).valid
    assert hasher.verify(PASSWORD, second).valid


def test_verify_accepts_the_right_password_and_rejects_others() -> None:
    stored = hasher.hash(PASSWORD)

    assert hasher.verify(PASSWORD, stored).valid
    assert not hasher.verify(PASSWORD + " ", stored).valid
    assert not hasher.verify(PASSWORD.upper(), stored).valid
    assert not hasher.verify("", stored).valid


def test_current_hash_needs_no_upgrade() -> None:
    assert hasher.verify(PASSWORD, hasher.hash(PASSWORD)).new_hash is None


@pytest.mark.parametrize(
    ("stored_as", "typed_as"),
    [
        (to_fullwidth("correct horse staple"), "correct horse staple"),
        ("café crème brûlée", "cafe\u0301 cre\u0300me bru\u0302le\u0301e"),  # composed vs combining
    ],
)
def test_equivalent_unicode_forms_verify(stored_as: str, typed_as: str) -> None:
    assert stored_as != typed_as  # different code points, or this test proves nothing
    assert hasher.verify(typed_as, hasher.hash(stored_as)).valid


def test_without_normalization_the_forms_would_not_match() -> None:
    # Guards the guard: prove these inputs genuinely differ at the byte level.
    assert to_fullwidth("ab") == "\uff41\uff42"
    composed: str = "caf\u00e9"  # one code point: e with acute
    decomposed: str = "cafe\u0301"  # e + combining acute accent
    assert composed != decomposed


def test_outdated_parameters_are_upgraded_on_successful_login() -> None:
    old_hasher = PasswordHasher(time_cost=1, memory_cost_kib=8 * 1024, parallelism=1)
    old_hash = old_hasher.hash(PASSWORD)
    assert "m=8192,t=1" in old_hash

    result = hasher.verify(PASSWORD, old_hash)

    assert result.valid
    assert result.new_hash is not None
    assert result.new_hash.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert hasher.verify(PASSWORD, result.new_hash) == result.__class__(valid=True)


def test_wrong_password_never_produces_an_upgraded_hash() -> None:
    old_hash = PasswordHasher(time_cost=1, memory_cost_kib=8 * 1024).hash(PASSWORD)

    assert hasher.verify("wrong password here", old_hash).new_hash is None


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "not-a-hash",
        "$2b$12$abcdefghijklmnopqrstuuv0wxyz12345678901234567890123",
        "$argon2id$garbage",
    ],
)
def test_corrupt_or_foreign_hashes_fail_verification_without_crashing(stored: str) -> None:
    assert hasher.verify(PASSWORD, stored).valid is False


def _median_seconds(fn: object, runs: int = 5) -> float:
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        fn()  # type: ignore[operator]
        samples.append(time.perf_counter() - started)
    return statistics.median(samples)


def test_dummy_verify_costs_about_the_same_as_a_real_verify() -> None:
    stored = hasher.hash(PASSWORD)
    hasher.dummy_verify("warm-up")  # first call also creates the dummy hash

    real = _median_seconds(lambda: hasher.verify("some password guess", stored))
    dummy = _median_seconds(lambda: hasher.dummy_verify("some password guess"))

    # Generous bounds: the point is "same order of magnitude", not microsecond equality.
    assert 0.5 < dummy / real < 2.0


@pytest.mark.anyio
async def test_async_hashing_does_not_block_the_event_loop() -> None:
    ticks = 0
    stop = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.001)

    ticker_task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    ticks_before = ticks
    hashes = [await hasher.hash_async(PASSWORD) for _ in range(5)]
    ticks_during = ticks - ticks_before
    stop.set()
    await ticker_task

    results = [await hasher.verify_async(PASSWORD, h) for h in hashes]
    assert all(result.valid for result in results)
    # Five hashes take well over 50 ms; a blocked loop would tick ~0 times meanwhile.
    assert ticks_during >= 10


# --- policy ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("password", "email", "expected_types"),
    [
        ("short", None, ["password_too_short"]),
        ("x" * (PASSWORD_MAX_LENGTH + 1), None, ["password_too_long"]),
        (" " * PASSWORD_MIN_LENGTH, None, ["password_blank"]),
        ("Alice.Smith@Example.com", "alice.smith@example.com", ["password_matches_email"]),
        ("alice.smith1", "Alice.Smith1@example.com", ["password_matches_email"]),
        ("alice", "alice@example.com", ["password_too_short", "password_matches_email"]),
    ],
)
def test_policy_violations(password: str, email: str | None, expected_types: list[str]) -> None:
    violations = password_policy_violations(password, email=email)

    assert [v["type"] for v in violations] == expected_types


@pytest.mark.parametrize(
    "password",
    [
        "correct horse battery staple",  # passphrases with spaces are welcome
        "a" * PASSWORD_MIN_LENGTH,  # boundary
        "b" * PASSWORD_MAX_LENGTH,  # boundary
        "mot de passe très sûr",  # non-ASCII
        "simple lowercase only",  # no composition rules (NIST 800-63B)
    ],
)
def test_policy_accepts_reasonable_passwords(password: str) -> None:
    assert password_policy_violations(password, email="alice@example.com") == []


def test_validate_password_raises_a_422_ready_error() -> None:
    with pytest.raises(ValidationFailedError) as exc_info:
        validate_password("short", email="alice@example.com")

    error = exc_info.value
    assert error.code == "PASSWORD_POLICY"
    assert error.details is not None
    fields = [FieldError.model_validate(f) for f in error.details["fields"]]
    assert [f.loc for f in fields] == [["body", "password"]]
    assert "short" not in error.message  # the password itself is never echoed
