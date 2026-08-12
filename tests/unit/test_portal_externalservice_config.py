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
DEFAULT_AUTH_POLICIES_PATH = (
    REPO_ROOT
    / "stacks/auth/auth/appdata/authentik/blueprints/25-default-auth-policies.yaml"
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
