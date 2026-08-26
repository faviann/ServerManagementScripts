#!/usr/bin/env python3
"""Disposable public-boundary harness for Portal's Traefik/Redis contract."""

from __future__ import annotations

import base64
import dataclasses
import enum
import hashlib
import http.client
import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "stacks/portal/traefik3/compose.yaml"
STATIC_CONFIG_PATH = (
    REPO_ROOT / "stacks/portal/traefik3/appdata/traefik3/config/traefik.yaml"
)
DYNAMIC_CONFIG_PATH = STATIC_CONFIG_PATH.parent / "conf.d"
LOCAL_HOST = "portal-entry.local.faviann.com"
REMOTE_HOSTS = (
    "bazarr.local.faviann.com",
    "jellyfin.local.faviann.com",
    "immich.local.faviann.com",
)


class AttemptScenario(enum.Enum):
    REDIS_READY = "redis-ready-before-provider"
    REDIS_RECREATED_DURING_START = "redis-unavailable-recreated-while-traefik-starts"
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
    redis_routes_before_input: dict[str, int | None] | None
    closed_watch_tree: bool
    evidence_directory: str
    route_timeout_seconds: float
    docker_server_version: str
    images: dict[str, str]
    tracked_config_mounts: dict[str, str]
    tracked_static_config_loaded: bool
    tracked_dynamic_config_loaded: bool


@dataclasses.dataclass(frozen=True)
class ComposeTransitionObservation:
    attempt_name: str
    compose_project: str
    redis_container_before: str
    redis_container_after: str
    traefik_container_before: str
    traefik_container_after: str
    traefik_running_while_redis_suspended: bool
    redis_seeded_at: str
    redis_suspended_at: str
    redis_resumed_at: str
    traefik_started_at: str
    local_route_status: int | None
    redis_route_statuses: dict[str, int | None]
    closed_watch_tree: bool
    image_ids: dict[str, str]
    tracked_compose_sha256: str
    docker_server_version: str
    evidence_directory: str


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
            f"docker {' '.join(args)} failed\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def _compose(
    project: str,
    overrides: tuple[Path, ...],
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--file",
        str(COMPOSE_PATH),
    ]
    for override in overrides:
        command.extend(("--file", str(override)))
    command.extend(args)
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **os.environ,
            "CF_DNS_API_TOKEN": "<REPLACE_ME>",
            "TRAEFIK_DASHBOARD_CREDENTIALS": "<REPLACE_ME>",
        },
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"docker compose {' '.join(args)} failed\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def _container_logs(container: str, tracked_log: Path | None = None) -> str:
    result = _docker("logs", container, check=False)
    logs = result.stdout + result.stderr
    if tracked_log is not None and tracked_log.exists():
        logs += tracked_log.read_text(encoding="utf-8")
    return logs


def _wait_for_redis(container: str) -> None:
    for _ in range(60):
        result = _docker("exec", container, "redis-cli", "ping", check=False)
        if result.returncode == 0 and result.stdout.strip() == "PONG":
            return
        time.sleep(0.25)
    raise AssertionError(f"Redis container {container} did not become ready")


def _seed_remote_routes(container: str) -> None:
    for host in REMOTE_HOSTS:
        name = host.partition(".")[0]
        values = {
            f"traefik/http/routers/{name}/rule": f"Host(`{host}`)",
            f"traefik/http/routers/{name}/service": name,
            f"traefik/http/services/{name}/loadbalancer/servers/0/url": (
                "http://backend:8081/ping"
            ),
        }
        for key, value in values.items():
            _docker("exec", container, "redis-cli", "SET", key, value)


def _probe(port: int, host: str, path: str) -> int | None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            with ssl._create_unverified_context().wrap_socket(
                connection,
                server_hostname=host,
            ) as secure_connection:
                secure_connection.sendall(
                    (
                        f"GET {path} HTTP/1.1\r\n"
                        f"Host: {host}\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode()
                )
                response = http.client.HTTPResponse(secure_connection)
                response.begin()
            return response.status
    except (OSError, TimeoutError):
        return None


def _observe_statuses(
    port: int,
    hosts: tuple[str, ...],
    timeout_seconds: float,
    expected_statuses: frozenset[int] = frozenset({200}),
    path: str = "/ping",
) -> dict[str, int | None]:
    deadline = time.monotonic() + timeout_seconds
    statuses: dict[str, int | None] = dict.fromkeys(hosts)
    while True:
        for host in hosts:
            if statuses[host] not in expected_statuses:
                statuses[host] = _probe(port, host, path)
        if all(status in expected_statuses for status in statuses.values()):
            return statuses
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return statuses
        time.sleep(min(0.25, remaining))


def _start_redis(
    name: str,
    network: str,
    image: str,
) -> None:
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


def _write_disposable_acme_storage(path: Path) -> None:
    certificate = path.parent / "disposable.crt"
    private_key = path.parent / "disposable.key"
    subject_alt_names = ",".join(
        [
            "DNS:faviann.com",
            "DNS:*.faviann.com",
            "DNS:*.admin.faviann.com",
            "DNS:*.home.faviann.com",
            "DNS:*.media.faviann.com",
            "DNS:*.public.faviann.com",
            "DNS:*.local.faviann.com",
        ]
    )
    result = subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "3650",
            "-subj",
            "/CN=faviann.com",
            "-addext",
            f"subjectAltName={subject_alt_names}",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"failed to create disposable TLS certificate: {result.stderr}"
        )
    private_key.chmod(0o600)
    storage = {
        "cloudflare": {
            "Account": {
                "Email": "placeholder@example.invalid",
                "Registration": None,
                "PrivateKey": base64.b64encode(private_key.read_bytes()).decode(),
                "KeyType": "4096",
            },
            "Certificates": [
                {
                    "domain": {
                        "main": "faviann.com",
                        "sans": [
                            "*.faviann.com",
                            "*.admin.faviann.com",
                            "*.home.faviann.com",
                            "*.media.faviann.com",
                            "*.public.faviann.com",
                            "*.local.faviann.com",
                        ],
                    },
                    "certificate": base64.b64encode(certificate.read_bytes()).decode(),
                    "key": base64.b64encode(private_key.read_bytes()).decode(),
                    "Store": "default",
                }
            ],
        }
    }
    path.write_text(json.dumps(storage), encoding="utf-8")
    path.chmod(0o600)


def _contract() -> dict[str, str]:
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
    assert compose["services"]["traefik"]["depends_on"] == [
        "traefik-docker-socket-proxy"
    ]
    assert "healthcheck" not in compose["services"]["redis"]
    return images


def _evidence_directory(attempt_name: str) -> Path:
    configured = os.environ.get("PORTAL_TRAEFIK_EVIDENCE_DIR")
    if configured:
        root = Path(configured)
        root.mkdir(parents=True, exist_ok=True)
        directory = root / attempt_name
        directory.mkdir(parents=False, exist_ok=False)
        return directory
    return Path(tempfile.mkdtemp(prefix=f"portal-traefik-{attempt_name}-"))


def run_compose_redis_replacement(
    attempt_name: str,
) -> ComposeTransitionObservation:
    """Run one pinned-Compose Redis replacement during Traefik startup."""
    evidence = _evidence_directory(attempt_name)
    token = uuid.uuid4().hex[:12]
    project = f"portal-traefik-recreation-{token}"
    runtime = Path(tempfile.mkdtemp(prefix=f"portal-traefik-recreation-{token}-"))
    logs_directory = runtime / "logs"
    certificates_directory = runtime / "certificates"
    override = runtime / "compose.runtime.yaml"
    logs_directory.mkdir(mode=0o700)
    certificates_directory.mkdir(mode=0o700)
    _write_disposable_acme_storage(
        certificates_directory / "cloudflare-acme.json"
    )
    images = _contract()
    override.write_text(
        "\n".join(
            [
                "services:",
                "  traefik-docker-socket-proxy:",
                "    container_name: !reset null",
                '    restart: "no"',
                "  traefik:",
                "    container_name: !reset null",
                '    restart: "no"',
                "    ports: !override",
                '      - "127.0.0.1::443/tcp"',
                "    volumes:",
                f"      - {STATIC_CONFIG_PATH.resolve()}:/etc/traefik/traefik.yaml:ro",
                f"      - {DYNAMIC_CONFIG_PATH.resolve()}:/etc/traefik/conf.d:ro",
                f"      - {certificates_directory}:/var/traefik/certs:rw",
                f"      - {logs_directory}:/logs:rw",
                "  redis:",
                "    container_name: !reset null",
                '    restart: "no"',
                "    ports: !reset []",
                "  backend:",
                f"    image: {images['traefik']}",
                "    command:",
                "      - --entrypoints.backend.address=:8081",
                "      - --ping=true",
                "      - --ping.entrypoint=backend",
                "    security_opt:",
                "      - no-new-privileges:true",
                "    labels:",
                '      traefik.enable: "true"',
                f'      traefik.http.routers.portal-entry.rule: "Host(`{LOCAL_HOST}`)"',
                "      traefik.http.services.portal-entry."
                "loadbalancer.server.port: 8081",
                "    networks:",
                "      - shared",
                "networks:",
                "  shared:",
                "    external: false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    override.chmod(0o600)
    overrides = (override,)
    shutil.copyfile(override, evidence / override.name)
    (evidence / override.name).chmod(0o600)

    redis_suspended = False
    try:
        effective_result = _compose(
            project, overrides, "config", "--format", "json"
        )
        (evidence / "compose-config.json").write_text(
            effective_result.stdout,
            encoding="utf-8",
        )
        _compose(project, overrides, "create")
        traefik_id_before = _compose(
            project, overrides, "ps", "--all", "--quiet", "traefik"
        ).stdout.strip()
        redis_id_before = _compose(
            project, overrides, "ps", "--all", "--quiet", "redis"
        ).stdout.strip()
        _docker("start", redis_id_before)
        _wait_for_redis(redis_id_before)
        _seed_remote_routes(redis_id_before)
        redis_seeded_at = datetime.now(timezone.utc).isoformat()
        _docker("kill", "--signal", "STOP", redis_id_before)
        redis_suspended_at = datetime.now(timezone.utc).isoformat()
        redis_suspended = True

        command = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--file",
            str(COMPOSE_PATH),
        ]
        for compose_override in overrides:
            command.extend(("--file", str(compose_override)))
        command.extend(("up", "--detach"))
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                **os.environ,
                "CF_DNS_API_TOKEN": "<REPLACE_ME>",
                "TRAEFIK_DASHBOARD_CREDENTIALS": "<REPLACE_ME>",
            },
        )
        deadline = time.monotonic() + 10
        running = "false"
        while time.monotonic() < deadline:
            running = _docker(
                "inspect", "--format", "{{.State.Running}}", traefik_id_before
            ).stdout.strip()
            if running == "true":
                break
            time.sleep(0.1)
        traefik_running_while_suspended = running == "true"
        provider_deadline = time.monotonic() + 15
        while time.monotonic() < provider_deadline:
            tracked_log = logs_directory / "traefik-container.log"
            if tracked_log.exists() and "Starting provider *redis.Provider" in (
                tracked_log.read_text(encoding="utf-8")
            ):
                break
            time.sleep(0.1)
        _docker("kill", "--signal", "CONT", redis_id_before)
        redis_resumed_at = datetime.now(timezone.utc).isoformat()
        redis_suspended = False
        _docker("rm", "-f", redis_id_before)
        _compose(project, overrides, "create", "redis")
        redis_id_after = _compose(
            project, overrides, "ps", "--all", "--quiet", "redis"
        ).stdout.strip()
        _docker("start", redis_id_after)
        _wait_for_redis(redis_id_after)
        _seed_remote_routes(redis_id_after)
        redis_seeded_at = datetime.now(timezone.utc).isoformat()
        stdout, stderr = process.communicate(timeout=60)
        (evidence / "compose-up.log").write_text(
            stdout + stderr,
            encoding="utf-8",
        )
        if process.returncode != 0:
            raise AssertionError(f"docker compose up failed: {stderr}")

        redis_inspect = json.loads(_docker("inspect", redis_id_after).stdout)[0]
        traefik_inspect = json.loads(
            _docker("inspect", traefik_id_before).stdout
        )[0]
        port = int(
            _docker(
                "inspect",
                "--format",
                '{{(index (index .NetworkSettings.Ports "443/tcp") 0).HostPort}}',
                traefik_id_before,
            ).stdout.strip()
        )
        local_status = _observe_statuses(port, (LOCAL_HOST,), 15)[LOCAL_HOST]
        redis_statuses = _observe_statuses(
            port,
            REMOTE_HOSTS,
            30,
        )
        logs = _container_logs(
            traefik_id_before,
            logs_directory / "traefik-container.log",
        )
        (evidence / "traefik.log").write_text(logs, encoding="utf-8")
        observation = ComposeTransitionObservation(
            attempt_name=attempt_name,
            compose_project=project,
            redis_container_before=redis_id_before,
            redis_container_after=redis_id_after,
            traefik_container_before=traefik_id_before,
            traefik_container_after=traefik_inspect["Id"],
            traefik_running_while_redis_suspended=(
                traefik_running_while_suspended
            ),
            redis_seeded_at=redis_seeded_at,
            redis_suspended_at=redis_suspended_at,
            redis_resumed_at=redis_resumed_at,
            traefik_started_at=traefik_inspect["State"]["StartedAt"],
            local_route_status=local_status,
            redis_route_statuses=redis_statuses,
            closed_watch_tree="watchtree channel is closed" in logs.lower(),
            image_ids={
                "redis": redis_inspect["Image"],
                "traefik": traefik_inspect["Image"],
            },
            tracked_compose_sha256=hashlib.sha256(
                COMPOSE_PATH.read_bytes()
            ).hexdigest(),
            docker_server_version=_docker(
                "version", "--format", "{{.Server.Version}}"
            ).stdout.strip(),
            evidence_directory=str(evidence),
        )
        (evidence / "observation.json").write_text(
            json.dumps(dataclasses.asdict(observation), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        return observation
    finally:
        if redis_suspended:
            _docker("kill", "--signal", "CONT", redis_id_before, check=False)
        down_result = _compose(
            project,
            overrides,
            "down",
            "--volumes",
            "--remove-orphans",
            check=False,
        )
        (evidence / "compose-down.log").write_text(
            down_result.stdout + down_result.stderr,
            encoding="utf-8",
        )
        shutil.rmtree(runtime, ignore_errors=True)


def run_attempt(
    attempt_name: str,
    scenario: AttemptScenario,
    route_timeout_seconds: float = 30,
) -> AttemptObservation:
    images = _contract()
    token = uuid.uuid4().hex[:12]
    shared_network = f"portal-traefik-shared-{token}"
    proxy_network = f"portal-traefik-proxy-{token}"
    redis = f"portal-traefik-redis-{token}"
    socket_proxy = f"portal-traefik-socket-{token}"
    traefik = f"portal-traefik-edge-{token}"
    backend = f"portal-traefik-backend-{token}"
    evidence = _evidence_directory(attempt_name)
    runtime = Path(tempfile.mkdtemp(prefix=f"portal-traefik-runtime-{token}-"))
    logs_directory = runtime / "logs"
    certificates_directory = runtime / "certificates"
    acme_storage = certificates_directory / "cloudflare-acme.json"
    tracked_log = logs_directory / "traefik-container.log"
    redis_routes_before_input: dict[str, int | None] | None = None

    try:
        logs_directory.mkdir(mode=0o700)
        certificates_directory.mkdir(mode=0o700)
        _write_disposable_acme_storage(acme_storage)
        _docker("network", "create", shared_network)
        _docker("network", "create", proxy_network)
        _docker(
            "run",
            "-d",
            "--rm",
            "--name",
            socket_proxy,
            "--network",
            proxy_network,
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

        _docker(
            "run",
            "-d",
            "--rm",
            "--name",
            backend,
            "--network",
            shared_network,
            "--network-alias",
            "backend",
            "--security-opt",
            "no-new-privileges:true",
            "--label",
            "traefik.enable=true",
            "--label",
            f"traefik.http.routers.portal-entry.rule=Host(`{LOCAL_HOST}`)",
            "--label",
            "traefik.http.services.portal-entry.loadbalancer.server.port=8081",
            "--label",
            f"traefik.docker.network={shared_network}",
            images["traefik"],
            "--entrypoints.backend.address=:8081",
            "--ping=true",
            "--ping.entrypoint=backend",
        )

        if scenario is AttemptScenario.REDIS_READY:
            _start_redis(
                redis,
                shared_network,
                images["redis"],
            )
            _seed_remote_routes(redis)
        elif scenario in {
            AttemptScenario.INPUT_AFTER_START,
            AttemptScenario.MISSING_PROVIDER_INPUT,
        }:
            _start_redis(
                redis,
                shared_network,
                images["redis"],
            )

        _docker(
            "create",
            "--name",
            traefik,
            "--network",
            shared_network,
            "--network-alias",
            "traefik",
            "-p",
            "127.0.0.1::80/tcp",
            "-p",
            "127.0.0.1::443/tcp",
            "-p",
            "127.0.0.1::443/udp",
            "--security-opt",
            "no-new-privileges:true",
            "-v",
            f"{STATIC_CONFIG_PATH.resolve()}:/etc/traefik/traefik.yaml:ro",
            "-v",
            f"{DYNAMIC_CONFIG_PATH.resolve()}:/etc/traefik/conf.d:ro",
            "-v",
            f"{logs_directory}:/logs:rw",
            "-v",
            f"{certificates_directory}:/var/traefik/certs:rw",
            "-e",
            "CF_DNS_API_TOKEN=<REPLACE_ME>",
            "-e",
            "TRAEFIK_DASHBOARD_CREDENTIALS=<REPLACE_ME>",
            images["traefik"],
        )
        container_before = _docker(
            "inspect", "--format", "{{.Id}}", traefik
        ).stdout.strip()
        _docker("network", "connect", proxy_network, traefik)

        _docker("start", traefik)

        port = int(
            _docker(
                "inspect",
                "--format",
                '{{(index (index .NetworkSettings.Ports "443/tcp") 0).HostPort}}',
                traefik,
            ).stdout.strip()
        )
        local_status = _observe_statuses(
            port,
            (LOCAL_HOST,),
            route_timeout_seconds,
        )[LOCAL_HOST]

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
        dynamic_status = _observe_statuses(
            port,
            ("traefik.local.faviann.com",),
            route_timeout_seconds,
            expected_statuses=frozenset({200, 301, 302, 403}),
            path="/dashboard/",
        )["traefik.local.faviann.com"]
        container_after = _docker(
            "inspect", "--format", "{{.Id}}", traefik
        ).stdout.strip()
        inspect = json.loads(_docker("inspect", traefik).stdout)[0]
        tracked_config_mounts = {
            mount["Destination"]: mount["Source"]
            for mount in inspect["Mounts"]
            if mount["Destination"] in {
                "/etc/traefik/traefik.yaml",
                "/etc/traefik/conf.d",
            }
        }
        logs = _container_logs(traefik, tracked_log)
        closed_watch_tree = "watchtree channel is closed" in logs.lower()
        (evidence / "traefik.log").write_text(logs, encoding="utf-8")

        observation = AttemptObservation(
            attempt_name=attempt_name,
            scenario=scenario.value,
            local_route_status=local_status,
            redis_route_statuses=redis_statuses,
            traefik_container_before=container_before,
            traefik_container_after=container_after,
            redis_routes_before_input=redis_routes_before_input,
            closed_watch_tree=closed_watch_tree,
            evidence_directory=str(evidence),
            route_timeout_seconds=route_timeout_seconds,
            docker_server_version=_docker(
                "version", "--format", "{{.Server.Version}}"
            ).stdout.strip(),
            images={
                name: _docker(
                    "image", "inspect", "--format", "{{.Id}}", image
                ).stdout.strip()
                for name, image in images.items()
            },
            tracked_config_mounts=tracked_config_mounts,
            tracked_static_config_loaded=(
                local_status == 200
                and all(
                    provider in logs
                    for provider in (
                        "Starting provider *file.Provider",
                        "Starting provider *docker.Provider",
                        "Starting provider *redis.Provider",
                    )
                )
            ),
            tracked_dynamic_config_loaded=(dynamic_status not in {None, 404}),
        )
        (evidence / "observation.json").write_text(
            json.dumps(dataclasses.asdict(observation), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        return observation
    finally:
        if not (evidence / "traefik.log").exists():
            (evidence / "traefik.log").write_text(
                _container_logs(traefik, tracked_log),
                encoding="utf-8",
            )
        for container in (traefik, backend, redis, socket_proxy):
            _docker("rm", "-f", container, check=False)
        _docker("network", "rm", shared_network, check=False)
        _docker("network", "rm", proxy_network, check=False)
        shutil.rmtree(runtime, ignore_errors=True)
