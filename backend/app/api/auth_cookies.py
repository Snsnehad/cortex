"""The refresh-token cookie (ADR-006).

Browsers keep the refresh token in a cookie that JavaScript cannot read:

* ``HttpOnly``: invisible to scripts, so an XSS bug cannot steal it.
* ``Secure`` (required in staging/production by settings validation): HTTPS only.
* ``SameSite=Strict``: never sent on cross-site requests (CSRF protection, together with the
  custom header the refresh endpoint requires in TASK-03.04).
* ``Path=/api/v1/auth``: sent only to the auth endpoints, not with every API call.
* ``Max-Age`` equals the token lifetime; clearing sets it to 0 with identical attributes,
  because browsers only delete a cookie whose path/domain match the original.
"""

from fastapi import Response

from app.config import Settings

REFRESH_COOKIE_NAME = "cortex_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=settings.auth.refresh_token_ttl_days * 24 * 60 * 60,
        path=REFRESH_COOKIE_PATH,
        secure=settings.auth.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        secure=settings.auth.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )
