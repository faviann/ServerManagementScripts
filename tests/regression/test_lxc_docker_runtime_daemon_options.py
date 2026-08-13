#!/usr/bin/env python3
"""Regression test: Ansible is the single writer of /etc/docker/daemon.json.

Runs the real config/lxc_docker_runtime role against a stub geerlingguy.docker
role and observes the daemon options the real role actually hands over, for a
GPU host, a non-GPU host, and a host where gpu_enabled is undefined.
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


# The fixture writes one file per observation; their presence proves the tagged
# assertions actually ran, so a --tags filter that selects nothing cannot pass.
EXPECTED_OBSERVATIONS = {
    "gpu_undefined.json",
    "gpu_undefined.mapping",
    "gpu_false.json",
    "gpu_false.mapping",
    "gpu_true.json",
    "gpu_true.mapping",
}


def run_isolated_playbook() -> tuple[subprocess.CompletedProcess[str], set[str]]:
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
                "--tags",
                "lxc_docker_runtime_daemon_options",
                "-e",
                f"temp_root={temp_root}",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        observed = {path.name for path in Path(temp_root).iterdir()}
        return result, observed & EXPECTED_OBSERVATIONS


def test_lxc_docker_runtime_declares_daemon_options_for_gpu_and_non_gpu() -> None:
    result, observed = run_isolated_playbook()

    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output
    missing = EXPECTED_OBSERVATIONS - observed
    assert not missing, (
        f"the tagged observations never ran; missing {sorted(missing)}\n{output}"
    )


if __name__ == "__main__":
    try:
        test_lxc_docker_runtime_declares_daemon_options_for_gpu_and_non_gpu()
    except AssertionError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
    print("ok: lxc_docker_runtime daemon options match the single-writer contract")
