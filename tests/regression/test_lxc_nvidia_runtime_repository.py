#!/usr/bin/env python3
"""Regression test for retryable NVIDIA repository publication.

Ansible exits 0 when a ``--tags`` selector matches no tasks, so a return code
alone cannot tell a real run apart from a run that asserted nothing. Each
scenario therefore also requires its own existing final semantic assertion task
to have run and passed, read out of the machine-readable report emitted by the
``ansible.posix.json`` stdout callback -- that callback ships in the
``ansible.posix`` collection, which ``collections/requirements.yml`` pins.

Matching the human-facing display with a regex was rejected because it renders
whatever the caller's environment asks for, and that changes without any change
here.

So ``run_isolated_playbook`` pins the display instead of tolerating it: JSON
callback, zero verbosity, no inherited extra callbacks, leaving stdout as
exactly one JSON document. That breaks if another writer still reaches stdout,
so the safer rule is that stdout which does not parse into the report fails the
test rather than passing it.

This guards each scenario's *final* semantic observation only -- that one task
is required to have run and passed. It does not prove that every assertion
inside a fixture ran.
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
ANSIBLE_PLAYBOOK = ansible_playbook_command(supplies_own_inventory=True)
PLAYBOOK = (
    REPO_ROOT
    / "tests"
    / "regression"
    / "fixtures"
    / "lxc_nvidia_runtime_repository_test.yml"
)
APT_ORDER_PLAYBOOK = (
    REPO_ROOT
    / "tests"
    / "regression"
    / "fixtures"
    / "lxc_nvidia_runtime_apt_order_test.yml"
)
FIXTURE_ROLES = (
    REPO_ROOT
    / "tests"
    / "regression"
    / "fixtures"
    / "lxc_nvidia_runtime_repository_assets"
    / "roles"
)


def run_isolated_playbook(
    playbook: Path, tags: str
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="lxc-nvidia-repository-") as temp_root:
        # ansible.cfg names a vault password file that must exist, but these
        # fixtures decrypt nothing: a per-scenario placeholder keeps the run
        # credential-free and independent of the caller's environment.
        vault_placeholder = Path(temp_root) / "vault-pass"
        vault_placeholder.write_text(
            "unused-fixture-placeholder\n", encoding="utf-8"
        )
        fixture_inventory = Path(temp_root) / "inventory.ini"
        fixture_inventory.write_text(
            "[local]\nlocalhost ansible_connection=local\n", encoding="utf-8"
        )
        repository_dir = Path(temp_root) / "repository"
        repository_dir.mkdir()

        env = os.environ.copy()
        env["ANSIBLE_VAULT_PASSWORD_FILE"] = str(vault_placeholder)
        env["ANSIBLE_INVENTORY"] = str(fixture_inventory)
        env["ANSIBLE_CACHE_PLUGIN_CONNECTION"] = str(Path(temp_root) / "fact-cache")
        env["ANSIBLE_LOCAL_TEMP"] = str(Path(temp_root) / "ansible-local-tmp")
        env["ANSIBLE_REMOTE_TEMP"] = str(Path(temp_root) / "ansible-tmp")
        env["ANSIBLE_ROLES_PATH"] = os.pathsep.join(
            [str(FIXTURE_ROLES), str(REPO_ROOT / "playbooks" / "roles")]
        )
        env["UV_CACHE_DIR"] = str(Path(temp_root) / "uv-cache")
        env["TMPDIR"] = temp_root
        env["ANSIBLE_STDOUT_CALLBACK"] = "ansible.posix.json"
        # Keep stdout to the report alone: an inherited verbosity prints a
        # config preamble ahead of it, and inherited callbacks interleave their
        # own lines around it. Neither overrides useful human debugging here,
        # because under the JSON callback stdout is a machine document anyway.
        env["ANSIBLE_VERBOSITY"] = "0"
        # Dropped rather than set empty: ansible-core parses an empty value as
        # the one-element list [""] and aborts on the empty plugin name, while
        # an absent variable falls back to ansible.cfg, which enables none.
        env.pop("ANSIBLE_CALLBACKS_ENABLED", None)
        result = subprocess.run(
            [
                "bwrap",
                "--ro-bind",
                "/",
                "/",
                # TMPDIR may be below /dev/shm, so establish /dev before
                # rebinding the scenario root that holds its prerequisites.
                "--dev",
                "/dev",
                "--bind",
                temp_root,
                temp_root,
                "--bind",
                str(repository_dir),
                "/etc/apt/sources.list.d",
                "--proc",
                "/proc",
                "--chdir",
                str(REPO_ROOT),
                *ANSIBLE_PLAYBOOK,
                str(playbook),
                "-f",
                "1",
                "--tags",
                tags,
                "-e",
                f"temp_root={temp_root}",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    return result


def summarize_failures(report: object, stdout: str) -> str:
    """Name the tasks the report marks failed or unreachable, and why."""
    lines: list[str] = []
    if isinstance(report, dict):
        for play in report.get("plays", []):
            for task in play.get("tasks", []):
                name = task.get("task", {}).get("name")
                for host, host_result in task.get("hosts", {}).items():
                    if not (
                        host_result.get("failed", False)
                        or host_result.get("unreachable", False)
                    ):
                        continue
                    lines.append(
                        f"  [{host}] {name}: {host_result.get('msg', '<no msg>')}"
                    )
    if not lines:
        return f"no failing task in its report. Raw stdout:\n{stdout}"
    return "failing tasks:\n" + "\n".join(lines)


def assert_observation_completed(
    result: subprocess.CompletedProcess[str], task_name: str
) -> None:
    """Require a successful run in which ``task_name`` actually ran and passed.

    The JSON report lists every task that started under ``plays[].tasks[]``
    with its name and a per-host outcome under ``hosts``. A task tag selection
    filtered out is absent from that list entirely; one that ran but was
    skipped carries ``skipped: true``, a failed one ``failed: true``, and an
    unreachable host ``unreachable: true`` -- so a genuine pass is the only
    outcome carrying none of the three. That reading holds only while stdout is
    the pinned report, so stdout that will not parse raises instead of passing.
    """
    stderr_note = (
        f"\ncaptured stderr:\n{result.stderr}" if result.stderr.strip() else ""
    )

    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = None

    if result.returncode != 0:
        raise AssertionError(
            f"ansible-playbook exited {result.returncode}, so the task "
            f"{task_name!r} did not run to a passing result: "
            f"{summarize_failures(report, result.stdout)}{stderr_note}"
        )

    if not isinstance(report, dict) or "plays" not in report:
        raise AssertionError(
            f"ansible-playbook exited 0, but the task {task_name!r} did not run "
            f"to a passing result: its stdout is not the pinned JSON callback "
            f"report, so nothing about the run can be confirmed. Raw stdout:\n"
            f"{result.stdout}{stderr_note}"
        )

    observed: list[str] = []
    for play in report.get("plays", []):
        for task in play.get("tasks", []):
            name = task.get("task", {}).get("name")
            if name not in observed:
                observed.append(name)
            if name != task_name:
                continue
            if any(
                not host_result.get("skipped", False)
                and not host_result.get("failed", False)
                and not host_result.get("unreachable", False)
                for host_result in task.get("hosts", {}).values()
            ):
                return

    cause = (
        "it started but every host result skipped, failed, or was unreachable"
        if task_name in observed
        else "it never started -- tag selection missed it, or it was renamed"
    )
    raise AssertionError(
        f"ansible-playbook exited 0, but the task {task_name!r} did not run to a "
        f"passing result: {cause}. Tasks observed: {observed}.{stderr_note}"
    )


def test_lxc_nvidia_runtime_repository_publication_is_retryable() -> None:
    result = run_isolated_playbook(PLAYBOOK, "lxc_nvidia_runtime_repository")

    assert_observation_completed(result, "Assert valid repository was not rewritten")


def test_lxc_nvidia_runtime_refreshes_apt_before_toolkit_install() -> None:
    result = run_isolated_playbook(
        APT_ORDER_PLAYBOOK,
        "lxc_nvidia_runtime_package_setup",
    )

    assert_observation_completed(
        result, "Assert cache refresh completed before isolated install failure"
    )


if __name__ == "__main__":
    try:
        test_lxc_nvidia_runtime_repository_publication_is_retryable()
        test_lxc_nvidia_runtime_refreshes_apt_before_toolkit_install()
    except AssertionError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
    print("ok: NVIDIA repository regression scenarios passed")
