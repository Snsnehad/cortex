"""Refresh-token generation, hashing and the cookie (TASK-03.03)."""

import re

from fastapi import Response

from app.api.auth_cookies import (
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
    clear_refresh_cookie,
    set_refresh_cookie,
)
from app.api.errors import status_for
from app.config import Settings
from app.config.settings import AuthSettings
from app.core.refresh_tokens import (
    InvalidRefreshTokenError,
    RefreshTokenExpiredError,
    generate_refresh_token,
    hash_refresh_token,
)


def test_tokens_are_long_random_and_url_safe() -> None:
    tokens = {generate_refresh_token() for _ in range(1000)}

    assert len(tokens) == 1000
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{43}", t) for t in tokens)  # 256 bits


def test_hash_is_deterministic_hex_and_differs_from_the_token() -> None:
    token = generate_refresh_token()

    assert hash_refresh_token(token) == hash_refresh_token(token)
    assert re.fullmatch(r"[0-9a-f]{64}", hash_refresh_token(token))
    assert hash_refresh_token(token) != token
    assert hash_refresh_token(token) != hash_refresh_token(generate_refresh_token())


def test_refresh_errors_are_401() -> None:
    assert status_for(InvalidRefreshTokenError()) == 401
    assert status_for(RefreshTokenExpiredError()) == 401


def cookie_header(response: Response) -> str:
    [header] = [v.decode() for k, v in response.raw_headers if k == b"set-cookie"]
    return header


def test_refresh_cookie_attributes() -> None:
    settings = Settings(
        _env_file=None, auth=AuthSettings(refresh_cookie_secure=True, refresh_token_ttl_days=14)
    )
    response = Response()

    set_refresh_cookie(response, "the-token", settings)
    header = cookie_header(response)

    assert header.startswith(f"{REFRESH_COOKIE_NAME}=the-token;")
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "SameSite=strict" in header
    assert f"Path={REFRESH_COOKIE_PATH}" in header
    assert f"Max-Age={14 * 24 * 3600}" in header


def test_secure_flag_follows_settings_for_local_http_development() -> None:
    response = Response()

    set_refresh_cookie(response, "t", Settings(_env_file=None))

    assert "Secure" not in cookie_header(response)  # dev default: plain http://localhost


def test_clearing_uses_the_same_path_and_expires_immediately() -> None:
    response = Response()

    clear_refresh_cookie(response, Settings(_env_file=None))
    header = cookie_header(response)

    assert header.startswith(f'{REFRESH_COOKIE_NAME}="";')
    assert "Max-Age=0" in header
    assert f"Path={REFRESH_COOKIE_PATH}" in header
