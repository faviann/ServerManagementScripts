#!/usr/bin/env python3
"""Repository contract tests for the workstation MCP auth proxy."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
STACK_ROOT = REPO_ROOT / "stacks" / "workstation" / "mcp-auth-proxy"
WORKSTATION_VARS_PATH = REPO_ROOT / "inventory" / "host_vars" / "workstation.yml"
PORTAL_VARS_PATH = REPO_ROOT / "inventory" / "host_vars" / "portal.yml"
AUTH_VARS_PATH = REPO_ROOT / "inventory" / "host_vars" / "auth.yml"
VAULT_EXAMPLE_PATH = REPO_ROOT / "inventory" / "group_vars" / "all" / "vault.yml.example"
TRAEFIK_CONFIG_PATH = (
    REPO_ROOT
    / "stacks"
    / "portal"
    / "traefik3"
    / "appdata"
    / "traefik3"
    / "config"
    / "conf.d"
    / "externalservice.yaml"
)


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_env_template(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )


def traefik_rule_selects_path(rule: str, path: str) -> bool:
    exact_paths = re.findall(r"Path\(`([^`]+)`\)", rule)
    prefixes = re.findall(r"PathPrefix\(`([^`]+)`\)", rule)
    return path in exact_paths or any(path.startswith(prefix) for prefix in prefixes)


def test_stack_runs_only_the_pinned_sigbit_proxy() -> None:
    compose = load_yaml(STACK_ROOT / "compose.yaml")

    assert set(compose["services"]) == {"mcp-auth-proxy"}
    assert (
        compose["services"]["mcp-auth-proxy"]["image"]
        == "ghcr.io/sigbit/mcp-auth-proxy:v2.10.2"
    )


def test_proxy_reaches_the_loopback_backend_without_rewriting_paths() -> None:
    compose = load_yaml(STACK_ROOT / "compose.yaml")
    proxy = compose["services"]["mcp-auth-proxy"]

    assert proxy["network_mode"] == "host"
    assert proxy["command"] == ["http://127.0.0.1:8080"]
    assert "ports" not in proxy


def test_local_oauth_repository_is_persistent() -> None:
    compose = load_yaml(STACK_ROOT / "compose.yaml")
    proxy = compose["services"]["mcp-auth-proxy"]

    assert compose["x-prereq-dirs"] == ["./appdata/data"]
    assert proxy["volumes"] == ["./appdata/data:/data"]


def test_proxy_environment_has_the_required_public_oidc_contract() -> None:
    env = load_env_template(STACK_ROOT / ".env.j2")

    assert env == {
        "EXTERNAL_URL": "https://mcp.faviann.com",
        "NO_AUTO_TLS": "true",
        "LISTEN": ":19081",
        "DATA_PATH": "/data",
        "REPOSITORY_BACKEND": "local",
        "OIDC_CONFIGURATION_URL": (
            "https://auth.faviann.com/application/o/moraine-mcp/"
            ".well-known/openid-configuration"
        ),
        "OIDC_CLIENT_ID": "moraine-mcp",
        "OIDC_CLIENT_SECRET": "{{ stack_vars.oidc_client_secret | replace('$', '$$') }}",
        "OIDC_SCOPES": "openid,profile,email",
        "TRUSTED_PROXIES": "{{ stack_vars.trusted_proxies }}",
        "AUTH_HMAC_SECRET": "{{ stack_vars.auth_hmac_secret | replace('$', '$$') }}",
        "JWT_PRIVATE_KEY": "'{{ stack_vars.jwt_private_key | replace('$', '$$') }}'",
        "PROXY_FORWARD_AUTHORIZATION": "false",
    }


def test_compose_loads_the_rendered_environment() -> None:
    compose = load_yaml(STACK_ROOT / "compose.yaml")
    proxy = compose["services"]["mcp-auth-proxy"]

    assert proxy["env_file"] == [".env"]
    assert proxy["restart"] == "unless-stopped"


def test_vault_and_portal_owned_values_have_render_bindings() -> None:
    workstation_vars = load_yaml(WORKSTATION_VARS_PATH)
    portal_vars = load_yaml(PORTAL_VARS_PATH)
    auth_vars = load_yaml(AUTH_VARS_PATH)
    vault_example = load_yaml(VAULT_EXAMPLE_PATH)

    assert workstation_vars["lxc_docker_env_stack_vars"]["mcp-auth-proxy"] == {
        "oidc_client_secret": "{{ vault_moraine_mcp_oidc_client_secret }}",
        "auth_hmac_secret": "{{ vault_workstation_mcp_auth_proxy_hmac_secret }}",
        "jwt_private_key": "{{ vault_workstation_mcp_auth_proxy_jwt_private_key }}",
        "trusted_proxies": "{{ hostvars['portal'].portal_traefik_source_cidr }}",
    }
    portal_source = ipaddress.ip_network(portal_vars["portal_traefik_source_cidr"])
    assert str(portal_source) == "10.1.0.2/32"
    assert portal_source.prefixlen == portal_source.max_prefixlen
    assert str(portal_source) not in {"0.0.0.0/0", "::/0"}
    assert workstation_vars["workstation_origin_firewall_allowed_hosts"] == ["portal"]
    assert auth_vars["lxc_docker_env_stack_vars"]["auth"][
        "moraine_mcp_oidc_client_secret"
    ] == "{{ vault_moraine_mcp_oidc_client_secret }}"
    assert {
        "vault_moraine_mcp_oidc_client_secret": "REPLACE_WITH_RANDOM_CLIENT_SECRET",
        "vault_workstation_mcp_auth_proxy_hmac_secret": "REPLACE_WITH_BASE64_32_BYTE_KEY",
        "vault_workstation_mcp_auth_proxy_jwt_private_key": "REPLACE_WITH_PEM_PKCS8_RSA_PRIVATE_KEY",
    }.items() <= vault_example.items()


def test_public_router_selects_only_mcp_and_proxy_oauth_paths() -> None:
    config = load_yaml(TRAEFIK_CONFIG_PATH)
    matching_routers = [
        name
        for name, router in config["http"]["routers"].items()
        if "mcp.faviann.com" in router["rule"]
    ]
    rule = config["http"]["routers"]["mcp-auth-proxy"]["rule"]

    assert matching_routers == ["mcp-auth-proxy"]
    assert "Host(`mcp.faviann.com`)" in rule
    assert "/.well-known/oauth-protected-resource/mcp" not in rule
    assert all(
        traefik_rule_selects_path(rule, path)
        for path in (
            "/mcp",
            "/.well-known/oauth-protected-resource",
            "/.idp/register",
            "/.auth/oidc/callback",
        )
    )
    assert not any(
        traefik_rule_selects_path(rule, path)
        for path in ("/", "/mcp/extra", "/api/v1/capabilities", "/api/v1/status")
    )


def test_public_router_preserves_paths_and_uses_the_firewalled_listener() -> None:
    config = load_yaml(TRAEFIK_CONFIG_PATH)
    router = config["http"]["routers"]["mcp-auth-proxy"]
    service = config["http"]["services"]["mcp-auth-proxy-workstation"]
    load_balancer = service["loadBalancer"]

    assert "middlewares" not in router
    assert router["priority"] == 1000
    assert load_balancer["servers"] == [
        {"url": "http://workstation.faviann.vms:19081"}
    ]
    assert load_balancer.get("passHostHeader", True) is True
    assert "passHostHeader" not in load_balancer


def test_listener_port_is_consistent_and_reserved_only_for_the_proxy() -> None:
    env = load_env_template(STACK_ROOT / ".env.j2")
    workstation_vars = load_yaml(WORKSTATION_VARS_PATH)
    config = load_yaml(TRAEFIK_CONFIG_PATH)

    assert env["LISTEN"] == ":19081"
    assert 19081 in workstation_vars["workstation_origin_firewall_protected_ports"]
    assert config["http"]["services"]["mcp-auth-proxy-workstation"]["loadBalancer"][
        "servers"
    ] == [{"url": "http://workstation.faviann.vms:19081"}]

    configured_paths = {
        path.relative_to(REPO_ROOT)
        for root in (REPO_ROOT / "inventory", REPO_ROOT / "stacks")
        for path in root.rglob("*")
        if path.is_file() and "19081" in path.read_text(encoding="utf-8", errors="ignore")
    }
    assert configured_paths == {
        Path("inventory/host_vars/workstation.yml"),
        Path("stacks/portal/traefik3/appdata/traefik3/config/conf.d/externalservice.yaml"),
        Path("stacks/workstation/mcp-auth-proxy/.env.j2"),
    }


def test_proxy_image_has_an_intentional_update_track() -> None:
    metadata = load_yaml(STACK_ROOT / "stack.yaml")

    assert metadata["updates"] == {"mode": "images", "track": "2.10"}
