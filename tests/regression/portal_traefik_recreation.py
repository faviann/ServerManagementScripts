#!/usr/bin/env python3
"""Disposable public-boundary harness for Portal's Traefik/Redis contract."""

from __future__ import annotations

import dataclasses
import enum
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "stacks/portal/traefik3/compose.yaml"
STATIC_CONFIG_PATH = (
    REPO_ROOT / "stacks/portal/traefik3/appdata/traefik3/config/traefik.yaml"
)
REMOTE_HOSTS = ("bazarr.test", "jellyfin.test", "immich.test")


class AttemptScenario(enum.Enum):
    REDIS_READY = "redis-ready-before-provider"
    REDIS_RECREATED = "redis-recreated-during-provider-start"
    INPUT_AFTER_START = "provider-input-after-startup"
    MISSING_PROVIDER_INPUT = "missing-provider-input"


@dataclasses.dataclass(frozen=True)
class AttemptObservation:
    attempt_name: str
    scenario: str
    local_route_status: int | None
    redis_route_statuses: dict[str, int | None]
    traefik_container_before: str
    traefik_container_after: str
    redis_container_before: str | None
    redis_container_after: str | None
    redis_routes_before_input: dict[str, int | None] | None
    closed_watch_tree: bool
    historical_incident: bool
    evidence_directory: str
    route_timeout_seconds: float
    docker_server_version: str
    images: dict[str, str]


def historical_incident_observed(
    *,
    closed_watch_tree: bool,
    redis_route_statuses: Mapping[str, int | None],
) -> bool:
    return closed_watch_tree and any(
        status != 200 for status in redis_route_statuses.values()
    )


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"docker {' '.join(args)} failed\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def _container_logs(container: str) -> str:
    result = _docker("logs", container, check=False)
    return result.stdout + result.stderr


def _wait_for_redis(container: str) -> None:
    for _ in range(60):
        result = _docker("exec", container, "redis-cli", "PING", check=False)
        if result.returncode == 0 and result.stdout.strip() == "PONG":
            return
        time.sleep(0.25)
    raise AssertionError(f"Redis container {container} did not become ready")


def _seed_remote_routes(container: str) -> None:
    for host in REMOTE_HOSTS:
        name = host.removesuffix(".test")
        values = {
            f"traefik/http/routers/{name}/rule": f"Host(`{host}`)",
            f"traefik/http/routers/{name}/entrypoints/0": "web",
            f"traefik/http/routers/{name}/service": name,
            f"traefik/http/services/{name}/loadbalancer/servers/0/url": (
                "http://traefik:8081/ping"
            ),
        }
        for key, value in values.items():
            _docker("exec", container, "redis-cli", "SET", key, value)


def _probe(port: int, host: str) -> int | None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/ping",
        headers={"Host": host},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (OSError, TimeoutError):
        return None


def _observe_statuses(
    port: int,
    hosts: tuple[str, ...],
    timeout_seconds: float,
) -> dict[str, int | None]:
    deadline = time.monotonic() + timeout_seconds
    statuses: dict[str, int | None] = dict.fromkeys(hosts)
    while True:
        for host in hosts:
            if statuses[host] != 200:
                statuses[host] = _probe(port, host)
        if all(status == 200 for status in statuses.values()):
            return statuses
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return statuses
        time.sleep(min(0.25, remaining))


def _start_redis(name: str, network: str, image: str) -> None:
    _docker(
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "--network",
        network,
        "--network-alias",
        "redis",
        "-e",
        "ALLOW_EMPTY_PASSWORD=yes",
        image,
    )
    _wait_for_redis(name)


def _contract() -> tuple[dict[str, str], str, str]:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    static = yaml.safe_load(STATIC_CONFIG_PATH.read_text(encoding="utf-8"))
    images = {
        name: service["image"]
        for name, service in compose["services"].items()
        if name in {"traefik", "redis", "traefik-docker-socket-proxy"}
    }
    assert static["providers"]["redis"]["endpoints"] == ["redis:6379"]
    assert static["providers"]["docker"]["endpoint"] == (
        "tcp://traefik-docker-socket-proxy:2375"
    )
    return images, "redis:6379", "tcp://traefik-docker-socket-proxy:2375"


def _evidence_directory(attempt_name: str) -> Path:
    configured = os.environ.get("PORTAL_TRAEFIK_EVIDENCE_DIR")
    if configured:
        root = Path(configured)
        root.mkdir(parents=True, exist_ok=True)
        directory = root / attempt_name
        directory.mkdir(parents=False, exist_ok=False)
        return directory
    return Path(tempfile.mkdtemp(prefix=f"portal-traefik-{attempt_name}-"))


def run_attempt(
    attempt_name: str,
    scenario: AttemptScenario,
    route_timeout_seconds: float = 30,
) -> AttemptObservation:
    images, redis_endpoint, docker_endpoint = _contract()
    token = uuid.uuid4().hex[:12]
    network = f"portal-traefik-{token}"
    redis = f"portal-traefik-redis-{token}"
    socket_proxy = f"portal-traefik-socket-{token}"
    traefik = f"portal-traefik-edge-{token}"
    evidence = _evidence_directory(attempt_name)
    redis_container_before: str | None = None
    redis_container_after: str | None = None
    redis_routes_before_input: dict[str, int | None] | None = None

    _docker("network", "create", network)
    try:
        _docker(
            "run",
            "-d",
            "--rm",
            "--name",
            socket_proxy,
            "--network",
            network,
            "--network-alias",
            "traefik-docker-socket-proxy",
            "-e",
            "CONTAINERS=1",
            "-e",
            "EVENTS=1",
            "-e",
            "INFO=1",
            "-e",
            "NETWORKS=1",
            "-e",
            "POST=0",
            "-v",
            "/run/docker.sock:/var/run/docker.sock:ro",
            images["traefik-docker-socket-proxy"],
        )

        if scenario in {AttemptScenario.REDIS_READY, AttemptScenario.REDIS_RECREATED}:
            _start_redis(redis, network, images["redis"])
            _seed_remote_routes(redis)
        elif scenario in {
            AttemptScenario.INPUT_AFTER_START,
            AttemptScenario.MISSING_PROVIDER_INPUT,
        }:
            _start_redis(redis, network, images["redis"])

        _docker(
            "run",
            "-d",
            "--rm",
            "--name",
            traefik,
            "--network",
            network,
            "--network-alias",
            "traefik",
            "-p",
            "127.0.0.1::8080",
            "--label",
            "traefik.enable=true",
            "--label",
            "traefik.http.routers.portal-entry.rule=Host(`portal-entry.test`)",
            "--label",
            "traefik.http.routers.portal-entry.entrypoints=web",
            "--label",
            "traefik.http.services.portal-entry.loadbalancer.server.port=8081",
            "--label",
            f"traefik.docker.network={network}",
            images["traefik"],
            "--log.level=DEBUG",
            "--entrypoints.web.address=:8080",
            "--entrypoints.ping.address=:8081",
            "--ping=true",
            "--ping.entrypoint=ping",
            f"--providers.docker.endpoint={docker_endpoint}",
            "--providers.docker.exposedbydefault=false",
            f"--providers.redis.endpoints={redis_endpoint}",
        )
        container_before = _docker("inspect", "--format", "{{.Id}}", traefik).stdout.strip()

        if scenario is AttemptScenario.REDIS_RECREATED:
            redis_container_before = _docker(
                "inspect", "--format", "{{.Id}}", redis
            ).stdout.strip()
            _docker("rm", "-f", redis)
            _start_redis(redis, network, images["redis"])
            redis_container_after = _docker(
                "inspect", "--format", "{{.Id}}", redis
            ).stdout.strip()
            _seed_remote_routes(redis)

        port = int(
            _docker(
                "inspect",
                "--format",
                '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}',
                traefik,
            ).stdout.strip()
        )
        local_status = _observe_statuses(
            port,
            ("portal-entry.test",),
            route_timeout_seconds,
        )["portal-entry.test"]

        if scenario is AttemptScenario.INPUT_AFTER_START:
            redis_routes_before_input = _observe_statuses(
                port,
                REMOTE_HOSTS,
                route_timeout_seconds,
            )
            _seed_remote_routes(redis)

        redis_statuses = _observe_statuses(
            port,
            REMOTE_HOSTS,
            route_timeout_seconds,
        )
        container_after = _docker("inspect", "--format", "{{.Id}}", traefik).stdout.strip()
        logs = _container_logs(traefik)
        closed_watch_tree = "watchtree channel is closed" in logs.lower()
        (evidence / "traefik.log").write_text(logs, encoding="utf-8")

        observation = AttemptObservation(
            attempt_name=attempt_name,
            scenario=scenario.value,
            local_route_status=local_status,
            redis_route_statuses=redis_statuses,
            traefik_container_before=container_before,
            traefik_container_after=container_after,
            redis_container_before=redis_container_before,
            redis_container_after=redis_container_after,
            redis_routes_before_input=redis_routes_before_input,
            closed_watch_tree=closed_watch_tree,
            historical_incident=historical_incident_observed(
                closed_watch_tree=closed_watch_tree,
                redis_route_statuses=redis_statuses,
            ),
            evidence_directory=str(evidence),
            route_timeout_seconds=route_timeout_seconds,
            docker_server_version=_docker(
                "version", "--format", "{{.Server.Version}}"
            ).stdout.strip(),
            images={
                name: _docker("image", "inspect", "--format", "{{.Id}}", image).stdout.strip()
                for name, image in images.items()
            },
        )
        (evidence / "observation.json").write_text(
            json.dumps(dataclasses.asdict(observation), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return observation
    finally:
        if not (evidence / "traefik.log").exists():
            (evidence / "traefik.log").write_text(
                _container_logs(traefik),
                encoding="utf-8",
            )
        for container in (traefik, redis, socket_proxy):
            _docker("rm", "-f", container, check=False)
        _docker("network", "rm", network, check=False)
