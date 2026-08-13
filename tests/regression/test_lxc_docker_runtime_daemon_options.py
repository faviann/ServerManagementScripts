#!/usr/bin/env python3
"""Regression test: Ansible is the single writer of /etc/docker/daemon.json.

Runs the real config/lxc_docker_runtime role against a stub geerlingguy.docker
role and observes the daemon options the real role actually hands over, for a
GPU host, a non-GPU host, and a host where gpu_enabled is undefined.

The fixture enters the role through its normal public entrypoint. External
Docker and systemd commands are replaced at the process boundary so exercising
the production wiring cannot inspect or change the workstation's Docker state.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = (
    REPO_ROOT
    / "tests"
    / "regression"
    / "fixtures"
    / "lxc_docker_runtime_daemon_options_test.yml"
)
FIXTURE_ROLES = (
    REPO_ROOT
    / "tests"
    / "regression"
    / "fixtures"
    / "lxc_docker_runtime_daemon_options_assets"
    # Not named "roles": ansible-lint would classify the geerlingguy.docker
    # stub as a repo role and reject its dotted name.
    / "role_stubs"
)
FIXTURE_SYSTEM_BIN = (
    REPO_ROOT
    / "tests"
    / "regression"
    / "fixtures"
    / "lxc_docker_runtime_daemon_options_assets"
    / "system_bin"
)


def run_isolated_playbook() -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="lxc-docker-daemon-options-") as temp_root:
        # ansible.cfg names a vault password file that must exist, but this
        # fixture decrypts nothing: placeholders keep the run credential-free.
        vault_placeholder = Path(temp_root) / "vault-pass"
        vault_placeholder.write_text("unused-fixture-placeholder\n", encoding="utf-8")
        fixture_inventory = Path(temp_root) / "inventory.ini"
        fixture_inventory.write_text(
            "[local]\nlocalhost ansible_connection=local\n", encoding="utf-8"
        )

        env = os.environ.copy()
        env["ANSIBLE_VAULT_PASSWORD_FILE"] = str(vault_placeholder)
        env["ANSIBLE_INVENTORY"] = str(fixture_inventory)
        env["ANSIBLE_CACHE_PLUGIN_CONNECTION"] = str(Path(temp_root) / "fact-cache")
        env["ANSIBLE_LOCAL_TEMP"] = str(Path(temp_root) / "ansible-local-tmp")
        env["ANSIBLE_REMOTE_TEMP"] = str(Path(temp_root) / "ansible-tmp")
        env["ANSIBLE_ROLES_PATH"] = os.pathsep.join(
            [str(FIXTURE_ROLES), str(REPO_ROOT / "playbooks" / "roles")]
        )
        env["TMPDIR"] = temp_root
        result = subprocess.run(
            [
                "uv",
                "run",
                "--locked",
                "ansible-playbook",
                str(PLAYBOOK),
                "-f",
                "1",
                "-e",
                f"temp_root={temp_root}",
                "-e",
                f"fixture_system_bin={FIXTURE_SYSTEM_BIN}",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        return result


def test_lxc_docker_runtime_declares_daemon_options_for_gpu_and_non_gpu() -> None:
    result = run_isolated_playbook()

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


if __name__ == "__main__":
    try:
        test_lxc_docker_runtime_declares_daemon_options_for_gpu_and_non_gpu()
    except AssertionError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
    print("ok: lxc_docker_runtime daemon options match the single-writer contract")
