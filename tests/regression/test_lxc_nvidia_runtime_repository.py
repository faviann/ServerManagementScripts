#!/usr/bin/env python3
"""Regression test for retryable NVIDIA repository publication.

Ansible exits 0 when a ``--tags`` selector matches no tasks, so a return code
alone cannot tell a real run apart from a run that asserted nothing. Each
scenario therefore also requires its own existing final semantic assertion task
to have run and passed, read out of the machine-readable report emitted by the
``ansible.builtin.json`` stdout callback.

Reading that report works because task names and per-host outcomes are fields
in a structured document. Reading the human-facing renderer instead breaks the
moment the caller's environment changes it -- verbosity inserts a task path,
color wraps the lines in escapes, an extra callback injects timings, a
different stdout callback drops banners, and Ansible itself prints a task-level
warning between a banner and its status line with no environment change at all.
The safer rule is to pin the JSON callback and parse it, never to regex the
display.

This guards each scenario's *final* semantic observation only -- that one task
is required to have run and passed. It does not prove that every assertion
inside a fixture ran.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
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
        repository_dir = Path(temp_root) / "repository"
        repository_dir.mkdir()

        env = os.environ.copy()
        env["ANSIBLE_CACHE_PLUGIN_CONNECTION"] = str(Path(temp_root) / "fact-cache")
        env["ANSIBLE_LOCAL_TEMP"] = str(Path(temp_root) / "ansible-local-tmp")
        env["ANSIBLE_REMOTE_TEMP"] = str(Path(temp_root) / "ansible-tmp")
        env["ANSIBLE_ROLES_PATH"] = os.pathsep.join(
            [str(FIXTURE_ROLES), str(REPO_ROOT / "playbooks" / "roles")]
        )
        env["UV_CACHE_DIR"] = str(Path(temp_root) / "uv-cache")
        env["TMPDIR"] = temp_root
        env["ANSIBLE_STDOUT_CALLBACK"] = "ansible.builtin.json"
        result = subprocess.run(
            [
                "bwrap",
                "--ro-bind",
                "/",
                "/",
                "--bind",
                temp_root,
                temp_root,
                "--bind",
                str(repository_dir),
                "/etc/apt/sources.list.d",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--chdir",
                str(REPO_ROOT),
                "uv",
                "run",
                "--locked",
                "ansible-playbook",
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


def assert_observation_completed(
    result: subprocess.CompletedProcess[str], task_name: str
) -> None:
    """Require a successful run in which ``task_name`` actually ran and passed.

    The JSON stdout callback lists every task that started under
    ``plays[].tasks[]`` with its name and a per-host outcome under ``hosts``.
    A task that tag selection filtered out is absent from that list entirely, a
    task that ran but was skipped carries ``skipped: true``, and a failed one
    carries ``failed: true`` -- so each of those stays distinguishable from a
    genuine pass no matter how the run is displayed. Other display lines can
    still share stdout -- verbosity banners ahead of the report, an extra
    enabled callback's timings around it -- so the report is located as an
    embedded document rather than assumed to be the whole stream. When no such
    document is there at all, that is treated as a failure rather than quietly
    as a pass.
    """
    stderr_note = (
        f"\ncaptured stderr:\n{result.stderr}" if result.stderr.strip() else ""
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    decoder = json.JSONDecoder()
    report = None
    offset = 0
    for line in result.stdout.splitlines(keepends=True):
        if line.startswith("{"):
            try:
                candidate, _ = decoder.raw_decode(result.stdout, offset)
            except ValueError:
                candidate = None
            if isinstance(candidate, dict) and "plays" in candidate:
                report = candidate
                break
        offset += len(line)

    if report is None:
        raise AssertionError(
            f"ansible-playbook exited 0, but its stdout carried no JSON "
            f"callback report, so the task {task_name!r} could not be confirmed "
            f"to have run.{stderr_note}"
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
                for host_result in task.get("hosts", {}).values()
            ):
                return

    cause = (
        "it started but every host result skipped or failed"
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
