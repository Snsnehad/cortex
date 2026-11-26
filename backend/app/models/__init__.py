"""SQLAlchemy ORM models, one module per aggregate.

Model registry: import every model module here. Alembic's env.py imports this package so
that autogenerate sees every table on ``Base.metadata``; a model that is not imported here
would be invisible to migrations (and the drift test in tests/integration/test_migrations.py
would not catch it).
"""

from app.models.organization import Organization, OrganizationMembership, OrgRole
from app.models.refresh_token import RefreshToken
from app.models.user import User

__all__ = [
    "OrgRole",
    "Organization",
    "OrganizationMembership",
    "RefreshToken",
    "User",
]
