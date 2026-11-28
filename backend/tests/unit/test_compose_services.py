"""The compose file's backend containers (TASK-05.06): opt-in, hardened, memory-limited.

These are the properties that keep the developer machine safe and light; a change that drops
one of them should fail here, not be noticed later in Docker Desktop.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[3] / "infra" / "compose" / "docker-compose.yml"
BACKEND = ("api", "worker", "beat")


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    config: dict[str, Any] = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return dict(config["services"])


def test_worker_and_beat_never_start_by_default(services: dict[str, Any]) -> None:
    # `docker compose up` (and `dev up`) starts only services without a profile.
    assert services["worker"]["profiles"] == ["workers"]
    assert services["beat"]["profiles"] == ["workers"]
    assert services["api"]["profiles"] == ["app"]
    assert {"postgres", "redis", "qdrant"} <= {
        n for n, s in services.items() if "profiles" not in s
    }


@pytest.mark.parametrize("name", BACKEND)
def test_backend_containers_are_hardened(services: dict[str, Any], name: str) -> None:
    service = services[name]
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "/tmp" in service["tmpfs"]  # noqa: S108 - a compose setting, not a temp file
    assert service["image"] == "cortex-backend:dev"


@pytest.mark.parametrize("name", BACKEND)
def test_backend_containers_use_the_compose_network(services: dict[str, Any], name: str) -> None:
    env = services[name]["environment"]
    assert env["CORTEX_DATABASE__HOST"] == "postgres"
    assert env["CORTEX_REDIS__URL"] == "redis://redis:6379/0"
    # Without this the API container would enqueue to 127.0.0.1 inside its own container.
    assert env["CORTEX_WORKER__BROKER_URL"] == "redis://redis:6379/1"


@pytest.mark.parametrize(("name", "limit"), [("worker", "512m"), ("beat", "192m")])
def test_background_containers_have_memory_limits(
    services: dict[str, Any], name: str, limit: str
) -> None:
    assert services[name]["mem_limit"] == limit


def test_worker_is_one_process_recycled_regularly(services: dict[str, Any]) -> None:
    command = services["worker"]["command"]
    # --quiet is a top-level option: after the subcommand celery refuses to start (this
    # exact mistake crash-looped both containers on the first try).
    assert command[:5] == ["celery", "--quiet", "-A", "app.workers.celery_app", "worker"]
    assert "--concurrency=1" in command
    assert "--max-tasks-per-child=50" in command
    assert services["worker"]["stop_grace_period"] == "60s"  # warm shutdown


def test_beat_keeps_its_state_on_the_tmpfs(services: dict[str, Any]) -> None:
    command = services["beat"]["command"]
    assert command[:5] == ["celery", "--quiet", "-A", "app.workers.celery_app", "beat"]
    assert "--schedule=/tmp/celerybeat-schedule" in command


@pytest.mark.parametrize("name", ["worker", "beat"])
def test_no_health_check_that_polls_a_port_they_do_not_serve(
    services: dict[str, Any], name: str
) -> None:
    assert services[name]["healthcheck"] == {"disable": True}
