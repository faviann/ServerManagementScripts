#!/usr/bin/env python3
"""Thin runner for the Ansible-native semantic lifecycle facade fixture."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "regression" / "fixtures"
ASSETS = FIXTURES / "lxc_lifecycle_facade_assets"
INVENTORY = FIXTURES / "lxc_lifecycle_facade_inventory.yml"
PLAYBOOKS = (
    FIXTURES / "lxc_lifecycle_decision_test.yml",
    FIXTURES / "lxc_lifecycle_plan_ephemerality_test.yml",
    FIXTURES / "lxc_lifecycle_execution_latch_test.yml",
)
ANSIBLE_PLAYBOOK = "uv run --locked ansible-playbook".split()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lxc-lifecycle-facade-") as temp_dir:
        temp_root = Path(temp_dir)
        state_dir = temp_root / "state"
        cache_dir = temp_root / "cache"
        home_dir = temp_root / "home"
        state_dir.mkdir()
        cache_dir.mkdir()
        (home_dir / ".ssh").mkdir(parents=True)

        env = os.environ.copy()
        env["PATH"] = f"{ASSETS / 'bin'}:{env['PATH']}"
        # One fork per scenario host: the semantic scenarios are isolated
        # per-vmid, so the whole matrix advances one task-wave at a time.
        env["ANSIBLE_FORKS"] = "25"
        # Rendering ~5000 ok/skipped results dominates controller time; hide
        # them. Failed tasks still print in full with their scenario names.
        env["ANSIBLE_DISPLAY_OK_HOSTS"] = "false"
        env["ANSIBLE_DISPLAY_SKIPPED_HOSTS"] = "false"
        env["LIFECYCLE_TEST_STATE_DIR"] = str(state_dir)
        env["ANSIBLE_ROLES_PATH"] = os.pathsep.join(
            [str(ASSETS / "roles"), str(REPO_ROOT / "playbooks" / "roles")]
        )
        env["ANSIBLE_COLLECTIONS_PATH"] = os.pathsep.join(
            [str(ASSETS / "collections"), str(REPO_ROOT / "collections")]
        )
        env["ANSIBLE_CACHE_PLUGIN_CONNECTION"] = str(cache_dir)
        # ansible.cfg names a vault password file that must exist, but the
        # fixture decrypts nothing: a placeholder keeps the run credential-free.
        vault_placeholder = temp_root / "vault-pass"
        vault_placeholder.write_text("unused-fixture-placeholder\n", encoding="utf-8")
        env["ANSIBLE_VAULT_PASSWORD_FILE"] = str(vault_placeholder)
        env["HOME"] = str(home_dir)

        for playbook in PLAYBOOKS:
            proc = subprocess.run(
                [
                    *ANSIBLE_PLAYBOOK,
                    "-i",
                    str(INVENTORY),
                    str(playbook),
                    "-e",
                    f"lifecycle_test_state_dir={state_dir}",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                env=env,
            )
            if proc.returncode != 0:
                print(f"{playbook.name} failed unexpectedly", file=sys.stderr)
                print(f"{proc.stdout}\n{proc.stderr}", file=sys.stderr)
                return 1

    print("ok: lifecycle facade publishes semantic results and plans remain command-local")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
