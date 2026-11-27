"""The verified tenant context (ADR-005).

An ``OrgContext`` exists only after the caller's membership in the organization has been
checked against the database (``app.dependencies.orgs``). Services receive it instead of a raw
organization id, so "which tenant am I acting for?" is never taken from client input.

Kept in ``core`` (not ``dependencies``) so services can use it without importing the web
layer.
"""

import uuid
from dataclasses import dataclass

from app.core.errors import NotFoundError
from app.models import OrgRole, User


@dataclass(frozen=True, slots=True)
class OrgContext:
    organization_id: uuid.UUID
    user: User
    role: OrgRole  # as of the membership check at the start of the request

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    def has_role(self, minimum: OrgRole) -> bool:
        return self.role.at_least(minimum)


class OrganizationNotFoundError(NotFoundError):
    """Unknown organization OR not a member: deliberately the same answer."""

    default_code = "ORGANIZATION_NOT_FOUND"
    default_message = "Organization not found."
