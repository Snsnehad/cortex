"""Organization context: the tenant boundary for every ``/orgs/{org_id}/...`` route (ADR-005).

The ``org_id`` in the URL is only a *claim*. ``OrgContextDep`` turns it into a verified context:

1. Authenticate the caller (``CurrentUser``): no or bad token → 401, before anything else, so
   an anonymous caller learns nothing about which organizations exist.
2. Look up the caller's membership in that organization (one indexed query). No membership,
   or no such organization → **404** ``ORGANIZATION_NOT_FOUND``, identical in both cases: the
   API never confirms that another tenant's organization exists.
3. The role comes from that row, on every request, so promotions, demotions and removals
   take effect immediately (they are never cached in the token).

Routes then use ``ctx.organization_id``, never the raw path value, for every query.

``require_role(OrgRole.ADMIN)`` (or the ``AdminContext`` / ``OwnerContext`` shorthands) adds:

4. Role below the minimum → **403** ``PERMISSION_DENIED`` (the caller *is* a member, so
   confirming the organization exists leaks nothing).
"""

import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

import structlog
from fastapi import Depends, Path

from app.core.errors import NotFoundError, PermissionDeniedError
from app.dependencies.auth import CurrentUser
from app.dependencies.database import DbSession
from app.models import OrgRole, User
from app.repositories.organizations import OrganizationRepository


@dataclass(frozen=True, slots=True)
class OrgContext:
    organization_id: uuid.UUID
    user: User
    role: OrgRole

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    def has_role(self, minimum: OrgRole) -> bool:
        return self.role.at_least(minimum)


class OrganizationNotFoundError(NotFoundError):
    default_code = "ORGANIZATION_NOT_FOUND"
    default_message = "Organization not found."


async def get_org_context(
    org_id: Annotated[uuid.UUID, Path(description="Organization id.")],
    user: CurrentUser,
    session: DbSession,
) -> OrgContext:
    membership = await OrganizationRepository(session).get_membership(org_id, user.id)
    if membership is None:
        raise OrganizationNotFoundError
    structlog.contextvars.bind_contextvars(organization_id=str(org_id))
    return OrgContext(organization_id=org_id, user=user, role=membership.role)


OrgContextDep = Annotated[OrgContext, Depends(get_org_context)]


def require_role(minimum: OrgRole) -> Callable[..., Coroutine[Any, Any, OrgContext]]:
    """Dependency factory: the verified org context, if the caller's role is high enough."""

    async def dependency(ctx: OrgContextDep) -> OrgContext:
        if not ctx.has_role(minimum):
            raise PermissionDeniedError(
                f"This action requires the {minimum.value} role or higher.",
                details={"required_role": minimum.value},
            )
        return ctx

    return dependency


AdminContext = Annotated[OrgContext, Depends(require_role(OrgRole.ADMIN))]
OwnerContext = Annotated[OrgContext, Depends(require_role(OrgRole.OWNER))]
