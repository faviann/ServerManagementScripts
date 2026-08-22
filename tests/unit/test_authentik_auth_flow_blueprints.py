#!/usr/bin/env python3
"""Unit tests for repo-managed Authentik authentication flow blueprints."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUTH_POLICIES_FILE = (
    REPO_ROOT
    / "stacks"
    / "auth"
    / "auth"
    / "appdata"
    / "authentik"
    / "blueprints"
    / "25-default-auth-policies.yaml"
)


class BlueprintLoader(yaml.SafeLoader):
    pass


def _construct_find(loader: BlueprintLoader, node: yaml.Node) -> tuple:
    return ("!Find", tuple(loader.construct_sequence(node, deep=True)))


def _construct_keyof(loader: BlueprintLoader, node: yaml.Node) -> tuple:
    return ("!KeyOf", loader.construct_scalar(node))


BlueprintLoader.add_constructor("!Find", _construct_find)
BlueprintLoader.add_constructor("!KeyOf", _construct_keyof)


def load_default_auth_policies() -> dict:
    return yaml.load(DEFAULT_AUTH_POLICIES_FILE.read_text(encoding="utf-8"), Loader=BlueprintLoader)


def find_ref(model: str, field: str, value: str) -> tuple:
    return ("!Find", (model, [field, value]))


class DefaultAuthPoliciesBlueprintTests(unittest.TestCase):
    def test_passwordless_flow_replaces_identification_with_user_login(self):
        blueprint = load_default_auth_policies()
        entries = blueprint["entries"]
        passwordless_flow = find_ref("authentik_flows.flow", "slug", "default-passwordless-flow")
        identification_stage = find_ref(
            "authentik_stages_identification.identificationstage",
            "name",
            "default-authentication-identification",
        )
        login_stage = find_ref(
            "authentik_stages_user_login.userloginstage",
            "name",
            "default-authentication-login",
        )

        stale_identification_bindings = [
            entry for entry in entries
            if entry.get("model") == "authentik_flows.flowstagebinding"
            and entry.get("state") == "absent"
            and entry.get("identifiers") == {
                "target": passwordless_flow,
                "stage": identification_stage,
                "order": 20,
            }
        ]
        replacement_login_bindings = [
            entry for entry in entries
            if entry.get("model") == "authentik_flows.flowstagebinding"
            and entry.get("state") == "present"
            and entry.get("identifiers") == {
                "target": passwordless_flow,
                "stage": login_stage,
                "order": 20,
            }
        ]

        self.assertEqual(len(stale_identification_bindings), 0)
        self.assertEqual(len(replacement_login_bindings), 1)


if __name__ == "__main__":
    unittest.main()
