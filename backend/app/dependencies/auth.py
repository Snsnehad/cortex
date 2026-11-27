"""Authentication dependencies.

``CurrentUser`` resolves the caller from ``Authorization: Bearer <access token>``:

1. no or malformed header → 401 ``UNAUTHENTICATED``
2. token expired → 401 ``TOKEN_EXPIRED`` (client should refresh)
3. token invalid → 401 ``INVALID_TOKEN``
4. user deleted or deactivated since the token was issued → 401 ``INVALID_TOKEN``

Step 4 hits the database on every request. That is deliberate: a deactivated account stops
working immediately, not when its token expires. It is a primary-key lookup.
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import AuthenticationError
from app.core.passwords import PasswordHasher
from app.core.tokens import AccessTokenService, InvalidTokenError
from app.dependencies.database import DbSession
from app.dependencies.settings import SettingsDep
from app.models import User
from app.services.auth import AuthService

# auto_error=False: we raise our own AuthenticationError so the response uses the standard
# error envelope. Registering HTTPBearer also adds the "Authorize" button to Swagger UI.
_bearer = HTTPBearer(auto_error=False, description="Access token from /auth/login.")


def get_access_tokens(request: Request) -> AccessTokenService:
    tokens: AccessTokenService = request.app.state.access_tokens
    return tokens


def get_password_hasher(request: Request) -> PasswordHasher:
    hasher: PasswordHasher = request.app.state.password_hasher
    return hasher


AccessTokensDep = Annotated[AccessTokenService, Depends(get_access_tokens)]
PasswordHasherDep = Annotated[PasswordHasher, Depends(get_password_hasher)]


async def get_current_user(
    session: DbSession,
    tokens: AccessTokensDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise AuthenticationError
    claims = tokens.verify(credentials.credentials)
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise InvalidTokenError
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_auth_service(
    session: DbSession,
    settings: SettingsDep,
    hasher: PasswordHasherDep,
    tokens: AccessTokensDep,
) -> AuthService:
    return AuthService(
        session,
        hasher=hasher,
        tokens=tokens,
        refresh_ttl_days=settings.auth.refresh_token_ttl_days,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
