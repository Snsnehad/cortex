"""Request/response models for /api/v1/auth (API_SPECIFICATION.md)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.models import OrgRole

# The password policy (length, email check) is enforced by app.core.passwords; this cap only
# stops absurd payloads before any hashing work.
RawPassword = Annotated[str, Field(min_length=1, max_length=1024)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: RawPassword
    full_name: Name
    organization_name: Name | None = None


class LoginRequest(BaseModel):
    # Plain string: an unusual address must fail with the generic credentials error, not a
    # 422 that reveals what the server considers a valid account email.
    email: Annotated[str, Field(min_length=1, max_length=320)]
    password: RawPassword


class UpdateMeRequest(BaseModel):
    full_name: Name


class ChangePasswordRequest(BaseModel):
    current_password: RawPassword
    new_password: RawPassword


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str


class MembershipOut(BaseModel):
    organization: OrganizationOut
    role: OrgRole


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  (OAuth token type, not a secret)
    expires_in: int = Field(description="Seconds until the access token expires.")


class RegisterResponse(TokenResponse):
    user: UserOut
    organization: OrganizationOut


class MeResponse(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]
