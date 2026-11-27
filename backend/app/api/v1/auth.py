"""/api/v1/auth: registration, login, token refresh, logout and the current account.

Routes only translate HTTP to service calls; all rules live in ``AuthService``.
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Header, Request, Response, status

from app.api.auth_cookies import REFRESH_COOKIE_NAME, clear_refresh_cookie, set_refresh_cookie
from app.core.errors import PermissionDeniedError
from app.dependencies.auth import AuthServiceDep, CurrentUser
from app.dependencies.settings import SettingsDep
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MembershipOut,
    MeResponse,
    OrganizationOut,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UpdateMeRequest,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])

CSRF_HEADER_VALUE = "cortex"
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)]


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=RegisterResponse)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDep,
    settings: SettingsDep,
) -> RegisterResponse:
    result = await auth.register(
        email=str(body.email),
        password=body.password,
        full_name=body.full_name,
        organization_name=body.organization_name,
        user_agent=_user_agent(request),
    )
    set_refresh_cookie(response, result.refresh.token, settings)
    return RegisterResponse(
        access_token=result.access.token,
        expires_in=result.access.expires_in,
        user=UserOut.model_validate(result.user),
        organization=OrganizationOut.model_validate(result.organization),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDep,
    settings: SettingsDep,
) -> TokenResponse:
    result = await auth.login(
        email=body.email, password=body.password, user_agent=_user_agent(request)
    )
    set_refresh_cookie(response, result.refresh.token, settings)
    return TokenResponse(access_token=result.access.token, expires_in=result.access.expires_in)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    auth: AuthServiceDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
    x_requested_with: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    # CSRF defence in depth (ADR-006): besides SameSite=Strict, require a custom header. A
    # cross-site form or link cannot set it, and cross-site fetch() needs a CORS preflight
    # that our CORS policy refuses.
    if x_requested_with != CSRF_HEADER_VALUE:
        raise PermissionDeniedError(
            "Missing or invalid X-Requested-With header.", code="CSRF_CHECK_FAILED"
        )
    access, issued = await auth.refresh(refresh_token, user_agent=_user_agent(request))
    set_refresh_cookie(response, issued.token, settings)
    return TokenResponse(access_token=access.token, expires_in=access.expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    auth: AuthServiceDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> None:
    await auth.logout(refresh_token)
    clear_refresh_cookie(response, settings)


async def _me(user: CurrentUser, auth: AuthServiceDep) -> MeResponse:
    memberships = await auth.memberships(user)
    return MeResponse(
        user=UserOut.model_validate(user),
        memberships=[
            MembershipOut(organization=OrganizationOut.model_validate(m.organization), role=m.role)
            for m in memberships
        ],
    )


@router.get("/me", response_model=MeResponse)
async def get_me(user: CurrentUser, auth: AuthServiceDep) -> MeResponse:
    return await _me(user, auth)


@router.patch("/me", response_model=MeResponse)
async def update_me(body: UpdateMeRequest, user: CurrentUser, auth: AuthServiceDep) -> MeResponse:
    await auth.update_profile(user, full_name=body.full_name)
    return await _me(user, auth)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser,
    auth: AuthServiceDep,
    refresh_token: RefreshCookie = None,
) -> None:
    await auth.change_password(
        user,
        current_password=body.current_password,
        new_password=body.new_password,
        current_refresh_token=refresh_token,
    )
