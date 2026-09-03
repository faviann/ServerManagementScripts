#!/usr/bin/env python3
"""Regression test: Ansible is the single writer of /etc/docker/daemon.json.

Runs the real config/lxc_docker_runtime role against a stub geerlingguy.docker
role and observes the daemon options the real role actually hands over, for a
GPU host, a non-GPU host, and a host where gpu_enabled is undefined.

The fixture enters the role through its normal public entrypoint. External
Docker and systemd commands are replaced at the process boundary so exercising
the production wiring cannot inspect or change the workstation's Docker state.

The launcher also requires every final semantic assertion to have run and
passed in Ansible's pinned machine-readable report. A zero process exit alone
is insufficient because Ansible can exit successfully when selection or a
conditional prevents the assertions from running.
"""

from __future__ import annotations

import json
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
REQUIRED_OBSERVATIONS = (
    "Assert geerlingguy.docker receives a real mapping",
    "Assert non-GPU hosts get daemon options with no runtimes key",
    "Assert GPU hosts get the NVIDIA runtime block in one pass",
)
ANSIBLE_PLAYBOOK = ansible_playbook_command(supplies_own_inventory=True)


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
        env["ANSIBLE_STDOUT_CALLBACK"] = "ansible.posix.json"
        env["ANSIBLE_VERBOSITY"] = "0"
        env.pop("ANSIBLE_CALLBACKS_ENABLED", None)
        env["TMPDIR"] = temp_root
        result = subprocess.run(
            [
                *ANSIBLE_PLAYBOOK,
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


def assert_observations_completed(result: subprocess.CompletedProcess[str]) -> None:
    """Require each final Ansible assertion to have a passing host result."""
    stderr_note = (
        f"\ncaptured stderr:\n{result.stderr}" if result.stderr.strip() else ""
    )
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = None

    if not isinstance(report, dict) or not isinstance(report.get("plays"), list):
        raise AssertionError(
            "daemon-options observations cannot be confirmed: stdout is not "
            "the pinned JSON callback report. Raw stdout:\n"
            f"{result.stdout}{stderr_note}"
        )

    outcomes: dict[str, list[object]] = {name: [] for name in REQUIRED_OBSERVATIONS}
    try:
        for play in report["plays"]:
            for task in play.get("tasks", []):
                name = task.get("task", {}).get("name")
                if name in outcomes:
                    outcomes[name].extend(task.get("hosts", {}).values())
    except (AttributeError, TypeError):
        raise AssertionError(
            "daemon-options observations cannot be confirmed: stdout is not "
            "the pinned JSON callback report. Raw stdout:\n"
            f"{result.stdout}{stderr_note}"
        ) from None

    failures = []
    for name, host_results in outcomes.items():
        passed = any(
            isinstance(host_result, dict)
            and not host_result.get("skipped", False)
            and not host_result.get("failed", False)
            and not host_result.get("unreachable", False)
            and host_result.get("action") == "ansible.builtin.assert"
            and host_result.get("changed") is False
            and host_result.get("msg") == "All assertions passed"
            for host_result in host_results
        )
        if passed:
            continue
        cause = (
            "was absent from the report"
            if not host_results
            else (
                "had no host result that passed "
                "(all were skipped, failed, or unreachable)"
            )
        )
        failures.append(f"{name!r} {cause}")

    if result.returncode != 0 or failures:
        process_failure = (
            f"ansible-playbook exited {result.returncode}; "
            if result.returncode != 0
            else ""
        )
        detail = "; ".join(failures) or "the required observations were present"
        raise AssertionError(
            f"{process_failure}daemon-options execution proof failed: {detail}."
            f"{stderr_note}"
        )


def test_lxc_docker_runtime_declares_daemon_options_for_gpu_and_non_gpu() -> None:
    result = run_isolated_playbook()

    assert_observations_completed(result)


if __name__ == "__main__":
    try:
        test_lxc_docker_runtime_declares_daemon_options_for_gpu_and_non_gpu()
    except AssertionError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
    print("ok: lxc_docker_runtime daemon options match the single-writer contract")
