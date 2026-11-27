# Testing

Test strategy and harness (TASK-02.08). Later sections: ingestion benchmark (TASK-07.07), retrieval evaluation (TASK-14.03/14.04), performance results (TASK-14.05).

## Running tests

From the repository root (or the equivalent `uv run pytest …` in `backend/`):

| Command | What runs | Needs Docker? |
|---|---|---|
| `scripts\dev.cmd test` | everything | yes (PostgreSQL, Redis, Qdrant up) |
| `scripts\dev.cmd test -m "not integration"` | unit tests only (~8 s) | **no**: verified with PostgreSQL stopped |
| `scripts\dev.cmd test tests/integration/test_pagination.py -k cursor` | a subset | yes |
| `scripts\dev.cmd check` | lint + types + all tests: what CI runs | yes |

## Layout

| Folder | Contents | Marker |
|---|---|---|
| `tests/unit/` | Pure Python, in-process app via httpx, fakes for external services | none |
| `tests/integration/` | Real PostgreSQL (scratch databases), Redis, Qdrant | `integration`, added **automatically** by folder (`tests/conftest.py`) |

Other markers: `slow`, `eval` (retrieval evaluation, opt-in), `llm` (real LLM calls, manual only). Markers are strict: a typo fails the run.

## Random order

**pytest-randomly** shuffles the test order on every run, locally and in CI. The seed is printed in the header (`Using --randomly-seed=486786529`).

- Reproduce an order-dependent failure: `pytest -p randomly --randomly-seed=486786529`
- Temporarily disable it: `pytest -p no:randomly`

Verified in TASK-02.08: the same seed gives the same order; different seeds give different orders; three full shuffled runs passed.

## Databases in tests

The development database is **never** touched by tests.

| Fixture (`tests/integration/conftest.py`) | Scope | What you get |
|---|---|---|
| `database_url` | test | An **empty** scratch database (migration tests) |
| `migrated_database_url` | session | One scratch database built by running the **real Alembic migrations**, so tests see exactly the schema production gets |
| `db_engine` | test | Async engine on the migrated DB, with a pool scoped to the test. **Every table is emptied afterwards** |
| `session_factory` | test | `async_sessionmaker` on `db_engine` |
| `test_settings` | test | App `Settings` pointing at the migrated scratch DB |
| `api_app` / `api_client` | test | The real FastAPI app on the scratch DB, plus an httpx client (in-process, no network) |

Scratch databases are named `cortex_migtest_*` / `cortex_modeltest_*` and dropped `WITH (FORCE)` at the end. CI creates and drops them too.

### Isolation strategy (measured, TASK-02.08)

Tests **commit for real**. Several properties can only be tested with separate, concurrent transactions (e.g. three simultaneous identical uploads: exactly one wins), so wrapping each test in a rolled-back transaction or savepoint is not an option.

After each test, all tables are emptied with one round trip of `DELETE` statements (children first). Measured on 12 tables with a pooled connection:

| Approach | Median |
|---|---|
| `TRUNCATE … CASCADE` | 60 ms |
| `DELETE` table by table | 29 ms |
| New connection (auth included), which `NullPool` paid for **every session** | 33 ms |

Switching from `NullPool` + `TRUNCATE` to a per-test pool + `DELETE` took the integration suite from **~43 s to ~28 s**. `test_every_test_starts_with_empty_tables` guards isolation: with random order, a test that leaks rows eventually runs before it and fails it.

## Building test data

**Factories** (`tests/integration/factories.py`) persist rows with unique defaults. Use them when values do not matter:

```python
owner = await create_user(session_factory)
org = await create_org(session_factory, owner=owner)        # owner gets an OWNER membership
await add_member(session_factory, org, await create_user(session_factory), OrgRole.ADMIN)
kb = await create_kb(session_factory, org)
doc = await create_document(session_factory, kb)
```

**Builders** (`tests/integration/db_helpers.py`: `new_user`, `new_org`, `new_kb`, `new_document`) return *unsaved* objects for constraint tests that need full control, e.g. a document with another tenant's `organization_id`. `violated_constraint(exc)` returns the constraint name PostgreSQL reported, so tests assert *which* rule fired.

## API tests against the database

```python
async def test_something(api_app, api_client, session_factory):
    owner = await create_user(session_factory)
    response = await api_client.get("/api/v1/...")
```

`api_client` talks to the real app (middleware, error handlers, dependencies) on the migrated scratch DB. `test_api_client_uses_the_scratch_database` proves it is not the dev DB.

## Conventions

- Async tests: `@pytest.mark.anyio` (or a module-level `pytestmark`). On Windows the fixtures use the selector event loop psycopg needs (ADR-018).
- Prefer asserting **specific** outcomes: the constraint name, the error `code`, exact counts.
- For important safety rules, prove the test has teeth: break the rule temporarily (e.g. weaken a migration), watch the test fail, restore byte-identically. Done for the tenant FKs, the active-job index, membership ownership, citation snapshots and the email index.
- Never call real paid APIs in automated tests; fakes and mocks only (`llm` marker for manual runs).

## Testing concurrency: force the interleaving (lesson from TASK-03.06)

Two in-process requests started with `asyncio.gather` **do not reliably overlap at the critical point**. In TASK-03.06 the first versions of the "two owners demote each other" and "two owners leave at once" tests **passed with the organization lock removed**: the first request finished its count-then-update before the second one counted. The tests looked green and proved nothing. Mutation testing exposed it.

The fix is to force the dangerous interleaving deterministically with a hook at the exact point that matters:

```python
barrier = asyncio.Barrier(2)
original = OrganizationRepository.count_owners

async def count_then_wait(self, organization_id):
    owners = await original(self, organization_id)
    with contextlib.suppress(TimeoutError, asyncio.BrokenBarrierError):
        await asyncio.wait_for(barrier.wait(), timeout=1.0)   # both must have counted
    return owners

monkeypatch.setattr(OrganizationRepository, "count_owners", count_then_wait)
```

- **Without the lock**, both requests count "2 owners" and pass the barrier together: the organization ends with 0 owners and the test fails (3/3 runs).
- **With the lock**, the second request is still waiting for the lock, the first gives up at the barrier after 1 s and finishes alone; the second then counts 1 and is refused.

Likewise, a "stale role" test that demoted the actor *before* the request never exercised the stale window (the org check at the start of the request already saw the new role). The working test demotes the actor from inside the `lock` call, i.e. after the org check and before the lock.

**Rule:** every concurrency test must be proven by a mutation that removes the protection it is about.
