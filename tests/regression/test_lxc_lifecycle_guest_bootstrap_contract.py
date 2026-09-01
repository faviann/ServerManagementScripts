#!/usr/bin/env python3
"""Regression test for normal lifecycle guest-bootstrap contract wiring."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ansible_test_helper import ansible_playbook_command


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = (
    REPO_ROOT
    / "tests"
    / "regression"
    / "fixtures"
    / "lxc_lifecycle_guest_bootstrap_contract_test.yml"
)
ANSIBLE_PLAYBOOK = ansible_playbook_command()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lxc-lifecycle-guest-bootstrap-") as temp_dir:
        temp_root = Path(temp_dir)
        mock_role_tasks = (
            temp_root
            / "infrastructure"
            / "lxc_ssh_key_injector"
            / "tasks"
        )
        mock_role_tasks.mkdir(parents=True)
        (mock_role_tasks / "main.yml").write_text(
            "---\n"
            "- name: Capture guest-bootstrap boundary input\n"
            "  ansible.builtin.set_fact:\n"
            "    _captured_guest_bootstrap: "
            '"{{ lxc_ssh_key_injector_guest_bootstrap }}"\n'
        )

        env = os.environ.copy()
        env["ANSIBLE_ROLES_PATH"] = os.pathsep.join(
            [str(temp_root), str(REPO_ROOT / "playbooks" / "roles")]
        )
        proc = subprocess.run(
            [*ANSIBLE_PLAYBOOK, str(PLAYBOOK)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
        )
        output = f"{proc.stdout}\n{proc.stderr}"

        if proc.returncode != 0:
            print("normal lifecycle guest-bootstrap wiring failed", file=sys.stderr)
            print(output, file=sys.stderr)
            return 1

    print("ok: normal lifecycle passes the compiled guest-bootstrap slice")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
