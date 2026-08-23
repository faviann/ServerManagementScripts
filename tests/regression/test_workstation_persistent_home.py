#!/usr/bin/env python3
"""Regression tests for workstation persistent home mounts."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from bitwarden_release_boundary import bitwarden_release_boundary


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests" / "regression" / "fixtures"
SUCCESS_PLAYBOOK = FIXTURE_ROOT / "workstation_persistent_home_success.yml"
DISABLED_PLAYBOOK = FIXTURE_ROOT / "workstation_persistent_home_disabled.yml"
CONFLICT_PLAYBOOK = FIXTURE_ROOT / "workstation_persistent_home_conflict.yml"
IDEMPOTENCY_PLAYBOOK = FIXTURE_ROOT / "workstation_persistent_home_idempotency.yml"
SYMLINK_MIGRATION_PLAYBOOK = FIXTURE_ROOT / "workstation_persistent_home_symlink_migration.yml"
ANSIBLE_PLAYBOOK = "uv run --locked ansible-playbook".split()


def run_playbook(
    playbook: Path,
    temp_root: str,
    *,
    check_mode: bool = False,
) -> subprocess.CompletedProcess[str]:
    with bitwarden_release_boundary() as bitwarden_args:
        command = [
            *ANSIBLE_PLAYBOOK,
            str(playbook),
            "-f",
            "1",
            "-e",
            f"temp_root={temp_root}",
            *bitwarden_args,
        ]
        if check_mode:
            command.append("--check")

        return subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )


def test_workstation_persistent_home_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="workstation-persistent-home-success-") as temp_root:
        success = run_playbook(SUCCESS_PLAYBOOK, temp_root)

    success_output = f"{success.stdout}\n{success.stderr}"
    assert success.returncode == 0, success_output

    with tempfile.TemporaryDirectory(prefix="workstation-persistent-home-disabled-") as temp_root:
        disabled = run_playbook(DISABLED_PLAYBOOK, temp_root)

    disabled_output = f"{disabled.stdout}\n{disabled.stderr}"
    assert disabled.returncode == 0, disabled_output

    with tempfile.TemporaryDirectory(prefix="workstation-persistent-home-conflict-") as temp_root:
        conflict = run_playbook(CONFLICT_PLAYBOOK, temp_root)

    conflict_output = f"{conflict.stdout}\n{conflict.stderr}"
    assert conflict.returncode != 0, conflict_output

    expected_markers = [
        "exists and is not the managed bind mount path",
        "Move or migrate it manually",
        ".claude",
    ]
    assert all(marker in conflict_output for marker in expected_markers), conflict_output

    with tempfile.TemporaryDirectory(prefix="workstation-persistent-home-symlink-migration-") as temp_root:
        migration = run_playbook(SYMLINK_MIGRATION_PLAYBOOK, temp_root)

    migration_output = f"{migration.stdout}\n{migration.stderr}"
    assert migration.returncode == 0, migration_output

    with tempfile.TemporaryDirectory(prefix="workstation-persistent-home-check-mode-") as temp_root:
        check_mode = run_playbook(IDEMPOTENCY_PLAYBOOK, temp_root, check_mode=True)

    check_mode_output = f"{check_mode.stdout}\n{check_mode.stderr}"
    assert check_mode.returncode == 0, check_mode_output

    with tempfile.TemporaryDirectory(prefix="workstation-persistent-home-idempotency-") as temp_root:
        first_idempotency = run_playbook(IDEMPOTENCY_PLAYBOOK, temp_root)
        second_idempotency = run_playbook(IDEMPOTENCY_PLAYBOOK, temp_root)

    first_idempotency_output = f"{first_idempotency.stdout}\n{first_idempotency.stderr}"
    assert first_idempotency.returncode == 0, first_idempotency_output

    second_idempotency_output = f"{second_idempotency.stdout}\n{second_idempotency.stderr}"
    assert second_idempotency.returncode == 0, second_idempotency_output
    assert "changed=0" in second_idempotency_output, second_idempotency_output
