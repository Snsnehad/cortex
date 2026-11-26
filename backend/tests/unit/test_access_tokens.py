"""Access tokens (TASK-03.02).

Most tests are attack-shaped: they build a malicious or broken token and assert it is refused
with the right code. Tokens that must be *validly signed* but wrong in one detail are built
with the service's private key on purpose; otherwise a failure could come from the
signature check alone and the claim checks would go untested.
"""

import base64
import json
import uuid
import warnings
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from jwt.warnings import InsecureKeyLengthWarning

from app.api.errors import status_for
from app.config import Settings
from app.core.tokens import (
    AUDIENCE,
    CLOCK_SKEW_LEEWAY,
    ISSUER,
    AccessTokenService,
    InvalidTokenError,
    TokenExpiredError,
)
from app.main import create_app

SECRET = "unit-test-jwt-secret-0123456789abcdef"
TTL = 900
USER_ID = uuid.uuid4()


def service(now: datetime | None = None) -> AccessTokenService:
    fixed = now
    return AccessTokenService(
        SECRET, ttl_seconds=TTL, clock=(lambda: fixed) if fixed else lambda: datetime.now(UTC)
    )


def signed(payload: dict[str, Any], *, algorithm: str = "HS256") -> str:
    """A token signed with the service's real (derived) key."""
    return jwt.encode(payload, service()._key, algorithm=algorithm)


def valid_payload(**changes: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(USER_ID),
        "iat": now,
        "exp": now + timedelta(seconds=TTL),
        "jti": uuid.uuid4().hex,
        "typ": "access",
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    payload.update(changes)
    return {k: v for k, v in payload.items() if v is not None}


def b64(data: dict[str, Any]) -> str:
    """Hand-built JWT segment (JWT encodes times as integer epoch seconds)."""
    plain = {k: int(v.timestamp()) if isinstance(v, datetime) else v for k, v in data.items()}
    return base64.urlsafe_b64encode(json.dumps(plain).encode()).rstrip(b"=").decode()


def assert_invalid(token: str) -> None:
    with pytest.raises(InvalidTokenError) as exc_info:
        service().verify(token)
    assert exc_info.value.code == "INVALID_TOKEN"


# --- happy path ----------------------------------------------------------------------------------


def test_issued_token_verifies_to_its_user() -> None:
    issued = service().issue(USER_ID)

    claims = service().verify(issued.token)

    assert claims.user_id == USER_ID
    assert issued.expires_in == TTL
    assert claims.expires_at - claims.issued_at == timedelta(seconds=TTL)
    assert claims.expires_at == issued.expires_at


def test_token_contains_only_the_user_id_and_protocol_claims() -> None:
    token = service().issue(USER_ID).token

    header = jwt.get_unverified_header(token)
    payload = jwt.decode(token, options={"verify_signature": False})

    assert header == {"alg": "HS256", "typ": "JWT"}
    assert set(payload) == {"sub", "iat", "exp", "jti", "typ", "iss", "aud"}
    assert payload["sub"] == str(USER_ID)  # no email, name or role: JWTs are readable


def test_every_token_has_a_unique_id() -> None:
    tokens = [service().issue(USER_ID) for _ in range(5)]

    assert len({service().verify(t.token).token_id for t in tokens}) == 5


# --- expiry and clock skew --------------------------------------------------------------------


def test_expired_token_is_reported_as_expired() -> None:
    long_ago = datetime.now(UTC) - timedelta(seconds=TTL) - CLOCK_SKEW_LEEWAY - timedelta(seconds=5)
    token = service(now=long_ago).issue(USER_ID).token

    with pytest.raises(TokenExpiredError) as exc_info:
        service().verify(token)
    assert exc_info.value.code == "TOKEN_EXPIRED"


def test_small_clock_skew_is_tolerated() -> None:
    # Expired 10 s ago according to this server, within the 30 s leeway.
    slightly_late = datetime.now(UTC) - timedelta(seconds=TTL + 10)
    token = service(now=slightly_late).issue(USER_ID).token

    assert service().verify(token).user_id == USER_ID


def test_token_issued_in_the_future_is_rejected() -> None:
    assert_invalid(service(now=datetime.now(UTC) + timedelta(minutes=10)).issue(USER_ID).token)


# --- forgery and tampering ----------------------------------------------------------------------


def test_tampered_payload_is_rejected() -> None:
    header, _, signature = service().issue(USER_ID).token.split(".")
    other_user = b64(valid_payload(sub=str(uuid.uuid4())))

    assert_invalid(f"{header}.{other_user}.{signature}")


def test_tampered_signature_is_rejected() -> None:
    token = service().issue(USER_ID).token
    flipped = token[:-2] + ("AA" if not token.endswith("AA") else "BB")

    assert_invalid(flipped)


def test_token_from_another_secret_is_rejected() -> None:
    other = AccessTokenService("a-different-secret-0123456789abcdef", ttl_seconds=TTL)

    assert_invalid(other.issue(USER_ID).token)


def test_token_signed_with_the_raw_secret_is_rejected() -> None:
    # Proves the signing key is derived: the configured secret itself cannot sign tokens.
    assert_invalid(jwt.encode(valid_payload(), SECRET, algorithm="HS256"))


def test_alg_none_is_rejected() -> None:
    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(valid_payload(iat=1, exp=4102444800))}."

    assert_invalid(unsigned)


def test_other_algorithms_are_rejected_even_when_correctly_signed() -> None:
    # PyJWT rightly warns that our 32-byte key is short for HS512; crafting this forged token
    # is the point of the test, so that one warning is expected here.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureKeyLengthWarning)
        token = signed(valid_payload(), algorithm="HS512")

    assert_invalid(token)


# --- claim checks (validly signed tokens) -------------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"typ": "refresh"}, id="wrong-typ"),
        pytest.param({"aud": "another-service"}, id="wrong-audience"),
        pytest.param({"iss": "someone-else"}, id="wrong-issuer"),
        pytest.param({"sub": "not-a-uuid"}, id="sub-not-uuid"),
        pytest.param({"jti": None}, id="missing-jti"),
        pytest.param({"typ": None}, id="missing-typ"),
        pytest.param({"aud": None}, id="missing-aud"),
        pytest.param({"exp": None}, id="missing-exp"),
    ],
)
def test_validly_signed_tokens_with_bad_claims_are_rejected(changes: dict[str, Any]) -> None:
    assert_invalid(signed(valid_payload(**changes)))


def test_the_valid_payload_helper_really_is_valid() -> None:
    # Guards the test above: its tokens fail only because of the one claim each changes.
    assert service().verify(signed(valid_payload())).user_id == USER_ID


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer x", "...."])
def test_malformed_tokens_are_rejected(garbage: str) -> None:
    assert_invalid(garbage)


# --- wiring and hygiene -----------------------------------------------------------------------


def test_token_errors_map_to_401() -> None:
    assert status_for(TokenExpiredError()) == 401
    assert status_for(InvalidTokenError()) == 401


def test_service_repr_never_contains_the_key() -> None:
    rendered = repr(service())

    assert SECRET not in rendered
    assert service()._key.hex() not in rendered


def test_short_secrets_are_refused() -> None:
    with pytest.raises(ValueError, match="too short"):
        AccessTokenService("short", ttl_seconds=TTL)


def test_app_exposes_a_configured_token_service() -> None:
    settings = Settings(_env_file=None)
    tokens = create_app(settings).state.access_tokens

    issued = tokens.issue(USER_ID)

    assert issued.expires_in == settings.auth.access_token_ttl_seconds
    assert tokens.verify(issued.token).user_id == USER_ID
