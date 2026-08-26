#!/usr/bin/env python3
"""Credential-free recreation of the Portal Traefik Redis-provider contract."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from portal_traefik_recreation import AttemptScenario, run_attempt


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", autouse=True)
def require_docker() -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail(
            "Docker executable is required for the Portal Traefik recreation",
            pytrace=False,
        )
    result = subprocess.run(
        [docker, "version", "--format", "{{.Server.Version}}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        pytest.fail(
            f"Docker daemon is required for the Portal Traefik recreation: {detail}",
            pytrace=False,
        )


@pytest.mark.parametrize(
    ("attempt_name", "scenario"),
    [
        ("01-redis-ready-before-provider", AttemptScenario.REDIS_READY),
        ("02-redis-recreated-during-start-1", AttemptScenario.REDIS_RECREATED),
        ("03-redis-recreated-during-start-2", AttemptScenario.REDIS_RECREATED),
        ("04-redis-recreated-during-start-3", AttemptScenario.REDIS_RECREATED),
        ("05-provider-input-after-startup", AttemptScenario.INPUT_AFTER_START),
    ],
    ids=lambda value: value.value if isinstance(value, AttemptScenario) else value,
)
def test_pinned_redis_routes_survive_portal_recreation(
    attempt_name: str,
    scenario: AttemptScenario,
) -> None:
    observation = run_attempt(attempt_name, scenario)

    assert observation.local_route_status == 200
    assert not observation.closed_watch_tree
    assert observation.redis_route_statuses == {
        "bazarr.local.faviann.com": 200,
        "jellyfin.local.faviann.com": 200,
        "immich.local.faviann.com": 200,
    }
    assert observation.traefik_container_before == observation.traefik_container_after
    assert observation.docker_server_version
    assert observation.tracked_config_mounts == {
        "/etc/traefik/traefik.yaml": str(
            REPO_ROOT
            / "stacks/portal/traefik3/appdata/traefik3/config/traefik.yaml"
        ),
        "/etc/traefik/conf.d": str(
            REPO_ROOT / "stacks/portal/traefik3/appdata/traefik3/config/conf.d"
        ),
    }
    assert observation.tracked_static_config_loaded
    assert observation.tracked_dynamic_config_loaded
    assert observation.redis_healthcheck_command == ["redis-cli", "ping"]
    assert observation.traefik_redis_dependency_condition == "service_healthy"
    if scenario is AttemptScenario.REDIS_RECREATED:
        assert observation.redis_container_before
        assert observation.redis_container_after
        assert observation.redis_container_before != observation.redis_container_after
    if scenario is AttemptScenario.INPUT_AFTER_START:
        assert observation.redis_routes_before_input == {
            "bazarr.local.faviann.com": 404,
            "jellyfin.local.faviann.com": 404,
            "immich.local.faviann.com": 404,
        }


def test_missing_redis_routes_still_write_complete_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PORTAL_TRAEFIK_EVIDENCE_DIR", str(tmp_path))

    observation = run_attempt(
        "missing-provider-input",
        AttemptScenario.MISSING_PROVIDER_INPUT,
        route_timeout_seconds=5,
    )
    evidence = tmp_path / "missing-provider-input"
    recorded = json.loads(
        (evidence / "observation.json").read_text(encoding="utf-8")
    )

    assert observation.local_route_status == 200
    assert observation.redis_route_statuses == {
        "bazarr.local.faviann.com": 404,
        "jellyfin.local.faviann.com": 404,
        "immich.local.faviann.com": 404,
    }
    assert recorded["redis_route_statuses"] == observation.redis_route_statuses
    assert recorded["traefik_container_before"]
    assert recorded["traefik_container_after"]
    assert recorded["docker_server_version"]
    assert recorded["route_timeout_seconds"] == 5
    assert set(recorded["images"]) == {
        "redis",
        "traefik",
        "traefik-docker-socket-proxy",
    }
    assert all(recorded["images"].values())
    assert isinstance(recorded["closed_watch_tree"], bool)
    assert (evidence / "traefik.log").read_text(encoding="utf-8")
