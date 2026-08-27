"""Shared construction of credential-free Ansible regression invocations."""

from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests/fixtures/ansible"
FIXTURE_INVENTORY = FIXTURE_ROOT / "inventory.yml"
FIXTURE_VAULT_PASSWORD_FILE = FIXTURE_ROOT / "vault-pass"
ANSIBLE_PLAYBOOK = ["uv", "run", "--locked", "ansible-playbook"]


def ansible_playbook_command(
    *arguments: str,
    supplies_own_inventory: bool = False,
) -> list[str]:
    """Return a locked playbook command under the validation fixture boundary."""
    if (
        not supplies_own_inventory
        and os.environ.get("ANSIBLE_INVENTORY") != str(FIXTURE_INVENTORY)
    ):
        raise AssertionError(
            "ANSIBLE_INVENTORY is outside the fixture environment; "
            "run this test through ./validate.sh tests"
        )
    if os.environ.get("ANSIBLE_VAULT_PASSWORD_FILE") != str(
        FIXTURE_VAULT_PASSWORD_FILE
    ):
        raise AssertionError(
            "ANSIBLE_VAULT_PASSWORD_FILE is outside the fixture environment; "
            "run this test through ./validate.sh tests"
        )
    return [*ANSIBLE_PLAYBOOK, *arguments]
