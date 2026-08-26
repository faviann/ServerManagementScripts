#!/usr/bin/env python3
"""Credential-free recreation of the Portal Traefik Redis-provider contract."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from portal_traefik_recreation import (
    AttemptScenario,
    historical_incident_observed,
    run_attempt,
)


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
    ("closed_watch_tree", "route_statuses", "expected"),
    [
        (True, {"bazarr.test": 404, "jellyfin.test": 200}, True),
        (True, {"bazarr.test": 200, "jellyfin.test": 200}, False),
        (False, {"bazarr.test": 404, "jellyfin.test": 200}, False),
    ],
)
def test_historical_incident_requires_diagnostic_and_missing_route(
    closed_watch_tree: bool,
    route_statuses: dict[str, int],
    expected: bool,
) -> None:
    assert historical_incident_observed(
        closed_watch_tree=closed_watch_tree,
        redis_route_statuses=route_statuses,
    ) is expected


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
    assert not observation.historical_incident
    assert observation.redis_route_statuses == {
        "bazarr.test": 200,
        "jellyfin.test": 200,
        "immich.test": 200,
    }
    assert observation.traefik_container_before == observation.traefik_container_after
    assert observation.docker_server_version
    if scenario is AttemptScenario.REDIS_RECREATED:
        assert observation.redis_container_before
        assert observation.redis_container_after
        assert observation.redis_container_before != observation.redis_container_after
    if scenario is AttemptScenario.INPUT_AFTER_START:
        assert observation.redis_routes_before_input == {
            "bazarr.test": 404,
            "jellyfin.test": 404,
            "immich.test": 404,
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
        "bazarr.test": 404,
        "jellyfin.test": 404,
        "immich.test": 404,
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
    assert recorded["historical_incident"] is False
    assert (evidence / "traefik.log").read_text(encoding="utf-8")
