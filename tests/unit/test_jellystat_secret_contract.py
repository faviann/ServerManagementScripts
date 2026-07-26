#!/usr/bin/env python3
"""Static contract checks for Jellystat secret handling."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
STACK_ROOT = REPO_ROOT / "stacks/jellyfin/jellystat"


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


class JellystatSecretContractTests(unittest.TestCase):
    def test_inventory_and_template_use_vault_backed_stack_vars(self) -> None:
        jellyfin_vars = load_yaml(REPO_ROOT / "inventory/host_vars/jellyfin.yml")
        stack_vars = jellyfin_vars.get("lxc_docker_env_stack_vars", {}).get("jellystat", {})

        if stack_vars.get("jwt_secret") != "{{ vault_jellystat_jwt_secret }}":
            self.fail("Jellystat JWT must bind to vault_jellystat_jwt_secret")
        if stack_vars.get("postgres_password") != "{{ vault_jellystat_postgres_password }}":
            self.fail("Jellystat PostgreSQL password must bind to vault_jellystat_postgres_password")

        assignments = {}
        for line in (STACK_ROOT / ".env.j2").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                assignments[key] = value

        if assignments.get("JWT_SECRET") != "{{ stack_vars.jwt_secret | compose_env }}":
            self.fail("Jellystat JWT template must use its required stack_vars expression")
        if assignments.get("POSTGRES_PASSWORD") != "{{ stack_vars.postgres_password | compose_env }}":
            self.fail("Jellystat PostgreSQL template must use its required stack_vars expression")

    def test_vault_example_documents_required_jellystat_keys(self) -> None:
        vault_example = load_yaml(REPO_ROOT / "inventory/group_vars/all/vault.yml.example")

        self.assertEqual(
            vault_example.get("vault_jellystat_jwt_secret"),
            "REPLACE_WITH_RANDOM_JWT_SECRET",
        )
        self.assertEqual(
            vault_example.get("vault_jellystat_postgres_password"),
            "REPLACE_WITH_RANDOM_DATABASE_PASSWORD",
        )


if __name__ == "__main__":
    unittest.main()
