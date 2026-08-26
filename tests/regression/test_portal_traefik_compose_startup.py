#!/usr/bin/env python3
"""Real-Compose boundary for Portal's Redis-gated Traefik startup."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from portal_traefik_recreation import run_compose_startup_order


@pytest.fixture(scope="module", autouse=True)
def require_docker_compose() -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail(
            "Docker is required for the Portal Compose recreation",
            pytrace=False,
        )
    result = subprocess.run(
        [docker, "compose", "version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        pytest.fail(f"Docker Compose is required: {detail}", pytrace=False)


def test_tracked_compose_starts_traefik_after_redis_is_healthy() -> None:
    observation = run_compose_startup_order("compose-redis-health-gate")

    assert observation.redis_health_status == "healthy"
    assert observation.redis_healthy_at <= observation.traefik_started_at
    assert observation.traefik_running
    assert observation.redis_healthcheck == ["CMD", "redis-cli", "ping"]
    assert observation.traefik_redis_dependency_condition == "service_healthy"
    assert observation.used_tracked_compose
    assert observation.override_preserved_health_contract
    override = Path(observation.evidence_directory) / "compose.runtime.yaml"
    assert override.stat().st_mode & 0o777 == 0o600
    assert "healthcheck:" not in override.read_text(encoding="utf-8")
    assert "depends_on:" not in override.read_text(encoding="utf-8")
    for resource_type in ("container", "network"):
        result = subprocess.run(
            [
                "docker",
                resource_type,
                "ls",
                "--all" if resource_type == "container" else "--quiet",
                "--filter",
                f"label=com.docker.compose.project={observation.compose_project}",
                "--format",
                "{{.ID}}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0
        assert not result.stdout.strip()
