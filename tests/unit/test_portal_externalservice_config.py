#!/usr/bin/env python3
"""Contract tests for portal external Traefik services."""

from __future__ import annotations

import ipaddress
import re
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
EXTERNALSERVICE_PATH = (
    REPO_ROOT / "stacks/portal/traefik3/appdata/traefik3/config/conf.d/externalservice.yaml"
)
AUTH_MIDDLEWARES_PATH = (
    REPO_ROOT
    / "stacks/portal/traefik3/appdata/traefik3/config/conf.d/middleware-authentik.yaml"
)
DEFAULT_AUTH_POLICIES_PATH = (
    REPO_ROOT
    / "stacks/auth/auth/appdata/authentik/blueprints/25-default-auth-policies.yaml"
)
AUTH_PROVIDERS_PATH = (
    REPO_ROOT / "stacks/auth/auth/appdata/authentik/blueprints/30-providers.yaml"
)
AUTH_APPLICATIONS_PATH = (
    REPO_ROOT / "stacks/auth/auth/appdata/authentik/blueprints/40-applications.yaml"
)


class PortalExternalServiceConfigTests(unittest.TestCase):
    def test_aoe_external_route_contract(self) -> None:
        config = yaml.safe_load(EXTERNALSERVICE_PATH.read_text(encoding="utf-8"))

        self.assertEqual(
            config["http"]["routers"]["aoe"],
            {
                "rule": "Host(`aoe.local.faviann.com`)",
                "entryPoints": "websecure",
                "service": "aoe-workstation",
                "priority": 1000,
                "middlewares": ["local-ip-restriction"],
            },
        )
        self.assertEqual(
            config["http"]["services"]["aoe-workstation"],
            {
                "loadBalancer": {
                    "servers": [{"url": "http://workstation.faviann.vms:4001"}],
                }
            },
        )

    def test_openclaw_external_route_contract(self) -> None:
        config = yaml.safe_load(EXTERNALSERVICE_PATH.read_text(encoding="utf-8"))

        self.assertEqual(
            config["http"]["routers"]["authentik-outpost-ai"],
            {
                "rule": "Host(`ai.local.faviann.com`) && PathPrefix(`/outpost.goauthentik.io`)",
                "entryPoints": "websecure",
                "service": "authentik",
                "priority": 1001,
                "middlewares": ["sslheader"],
            },
        )
        self.assertEqual(
            config["http"]["routers"]["ai"],
            {
                "rule": "Host(`ai.local.faviann.com`)",
                "entryPoints": "websecure",
                "service": "openclaw-dashboard",
                "priority": 1000,
                "middlewares": ["local-ip-restriction", "protected-edge-auth@file", "openclaw-operator-scopes"],
            },
        )
        self.assertEqual(
            config["http"]["middlewares"]["openclaw-operator-scopes"],
            {
                "headers": {
                    "customRequestHeaders": {
                        "X-OpenClaw-Scopes": "operator.read,operator.write,operator.admin",
                    }
                }
            },
        )
        self.assertEqual(
            config["http"]["services"]["openclaw-dashboard"],
            {
                "loadBalancer": {
                    "servers": [{"url": "http://workstation.faviann.vms:18789"}],
                }
            },
        )

    def test_collie_external_route_contract(self) -> None:
        config = yaml.safe_load(EXTERNALSERVICE_PATH.read_text(encoding="utf-8"))
        auth_config = yaml.safe_load(
            AUTH_MIDDLEWARES_PATH.read_text(encoding="utf-8")
        )
        routers = config["http"]["routers"]
        auth_middlewares = auth_config["http"]["middlewares"]

        self.assertEqual(
            routers["collie"],
            {
                "rule": "Host(`collie.admin.faviann.com`)",
                "entryPoints": "websecure",
                "service": "collie-workstation",
                "priority": 1000,
                "middlewares": [
                    "local-ip-restriction",
                    "protected-edge-auth@file",
                ],
            },
        )
        self.assertEqual(
            config["http"]["services"]["collie-workstation"],
            {
                "loadBalancer": {
                    "servers": [{"url": "http://workstation.faviann.vms:8788"}],
                }
            },
        )

        recovery_router = routers["collie-auth-recovery"]
        self.assertEqual(
            recovery_router,
            {
                "rule": (
                    "Host(`collie.admin.faviann.com`) && PathPrefix(`/auth/`)"
                ),
                "entryPoints": "websecure",
                "service": "authentik",
                "priority": 1002,
                "middlewares": [
                    "local-ip-restriction",
                    "collie-auth-recovery",
                ],
            },
        )
        self.assertGreater(recovery_router["priority"], routers["collie"]["priority"])
        self.assertNotEqual(recovery_router["service"], "collie-workstation")
        self.assertEqual(
            routers["authentik-outpost-admin-subdomains"],
            {
                "rule": (
                    "HostRegexp(`^[a-z0-9-]+\\.admin\\.faviann\\.com$`) "
                    "&& PathPrefix(`/outpost.goauthentik.io`)"
                ),
                "entryPoints": "websecure",
                "service": "authentik",
                "priority": 1001,
                "middlewares": ["sslheader"],
            },
        )
        self.assertEqual(
            config["http"]["middlewares"]["collie-auth-recovery"],
            {
                "redirectRegex": {
                    "regex": r"^https://collie\.admin\.faviann\.com/auth/.*$",
                    "replacement": (
                        "https://collie.admin.faviann.com/"
                        "outpost.goauthentik.io/start?"
                        "rd=https%3A%2F%2Fcollie.admin.faviann.com%2F"
                    ),
                    "permanent": False,
                }
            },
        )
        self.assertEqual(
            [
                name
                for name, router in routers.items()
                if "collie.admin.faviann.com" in router["rule"]
                and router["service"] == "collie-workstation"
            ],
            ["collie"],
        )

        # Traefik preserves Host by default, and Origin is passed through unless a
        # headers middleware rewrites it. The exact route contract intentionally
        # contains neither kind of rewrite.
        self.assertNotEqual(
            config["http"]["services"]["collie-workstation"]["loadBalancer"].get(
                "passHostHeader"
            ),
            False,
        )
        self.assertEqual(
            auth_middlewares["protected-edge-auth"]["chain"]["middlewares"],
            ["forwardAuth-authentik"],
        )
        forward_auth = auth_middlewares["forwardAuth-authentik"]
        self.assertIn("forwardAuth", forward_auth)
        self.assertEqual(
            forward_auth["forwardAuth"]["address"],
            "http://auth.faviann.vms:9000/outpost.goauthentik.io/auth/traefik",
        )
        resolved_middlewares = []
        for middleware_name in routers["collie"]["middlewares"]:
            middleware_name = middleware_name.removesuffix("@file")
            middleware = config["http"]["middlewares"].get(middleware_name)
            if middleware is None:
                middleware = auth_middlewares[middleware_name]
            resolved_middlewares.append(middleware)
            for chained_name in middleware.get("chain", {}).get("middlewares", []):
                resolved_middlewares.append(auth_middlewares[chained_name])
        for middleware in resolved_middlewares:
            custom_headers = middleware.get("headers", {}).get(
                "customRequestHeaders", {}
            )
            self.assertNotIn("Host", custom_headers)
            self.assertNotIn("Origin", custom_headers)

        providers = yaml.load(
            AUTH_PROVIDERS_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )["entries"]
        admin_provider = next(
            entry
            for entry in providers
            if entry.get("identifiers", {}).get("name")
            == "admin-wildcard-forwardauth"
        )
        self.assertEqual(admin_provider["state"], "present")
        self.assertEqual(admin_provider["attrs"]["mode"], "forward_domain")
        self.assertEqual(
            admin_provider["attrs"]["external_host"], "https://admin.faviann.com"
        )

        applications = yaml.load(
            AUTH_APPLICATIONS_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )["entries"]
        admin_application = next(
            entry for entry in applications if entry.get("id") == "app-admin-wildcard"
        )
        self.assertEqual(admin_application["state"], "present")
        self.assertEqual(
            admin_application["attrs"]["provider"],
            [
                "authentik_providers_proxy.proxyprovider",
                ["name", "admin-wildcard-forwardauth"],
            ],
        )
        self.assertEqual(
            [
                entry["attrs"]
                for entry in applications
                if entry.get("model") == "authentik_policies.policybinding"
                and entry.get("state") == "present"
                and entry.get("attrs", {}).get("target") == "app-admin-wildcard"
            ],
            [
                {
                    "target": "app-admin-wildcard",
                    "order": "0",
                    "enabled": "true",
                    "negate": "false",
                    "failure_result": "false",
                    "timeout": "30",
                    "group": ["authentik_core.group", ["name", "admins"]],
                }
            ],
        )

    def test_admin_source_policies_use_the_same_networks(self) -> None:
        config = yaml.safe_load(EXTERNALSERVICE_PATH.read_text(encoding="utf-8"))
        traefik_ranges = config["http"]["middlewares"]["local-ip-restriction"][
            "IPAllowList"
        ]["sourceRange"]
        traefik_networks = {
            ipaddress.ip_network(source_range, strict=False)
            for source_range in traefik_ranges
            if source_range != "127.0.0.1/32"
        }

        authentik_blueprint = DEFAULT_AUTH_POLICIES_PATH.read_text(encoding="utf-8")
        authentik_networks = {
            ipaddress.ip_network(source_range)
            for source_range in re.findall(r'ip_network\("([^"]+)"\)', authentik_blueprint)
        }

        self.assertEqual(traefik_networks, authentik_networks)
        self.assertIn(ipaddress.ip_network("10.200.196.0/24"), traefik_networks)
        self.assertNotIn("12.200.196.0/24", authentik_blueprint)


if __name__ == "__main__":
    unittest.main()
