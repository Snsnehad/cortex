"""SQLAlchemy ORM models, one module per aggregate.

Model registry: import every model module here. Alembic's env.py imports this package so
that autogenerate sees every table on ``Base.metadata``; a model that is not imported here
would be invisible to migrations (and the drift test in tests/integration/test_migrations.py
would not catch it). Model modules are added from TASK-02.03 onwards.
"""
