"""Shared helpers for API integration tests: requests as a user, a one-org team, codes."""

from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import OrgRole, User
from tests.integration.factories import add_member, create_org, create_user

Factory = async_sessionmaker[AsyncSession]


class Api:
    """Small helper: requests as a given user against /api/v1."""

    def __init__(self, app: FastAPI, client: httpx.AsyncClient) -> None:
        self._app, self._client = app, client

    async def __call__(
        self, user: User | None, method: str, path: str, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        headers = {}
        if user is not None:
            token = self._app.state.access_tokens.issue(user.id).token
            headers["Authorization"] = f"Bearer {token}"
        return await self._client.request(method, f"/api/v1{path}", json=json, headers=headers)


async def team(factory: Factory) -> dict[str, Any]:
    """An org with one user per role, plus an outsider who owns a different org."""
    owner = await create_user(factory)
    org = await create_org(factory, owner=owner, name="Acme")
    admin, member, outsider = [await create_user(factory) for _ in range(3)]
    await add_member(factory, org, admin, OrgRole.ADMIN)
    await add_member(factory, org, member, OrgRole.MEMBER)
    await create_org(factory, owner=outsider)
    return {"org": org, "owner": owner, "admin": admin, "member": member, "outsider": outsider}


def code(response: httpx.Response) -> tuple[int, str | None]:
    error = response.json().get("error") if response.content else None
    return response.status_code, error["code"] if error else None
