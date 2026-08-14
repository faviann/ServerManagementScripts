#!/usr/bin/env python3
"""Regression test for retryable NVIDIA repository publication.

Ansible exits 0 when a ``--tags`` selector matches no tasks, so a return code
alone cannot tell a real run apart from a run that asserted nothing. Each
scenario therefore also requires its own existing final semantic assertion task
to be visible as *completed* in the captured output.
"""

from __future__ import annotations

import os
import re
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
        # The observation below asserts on how the default stdout callback
        # renders task banners, so that rendering is part of this fixture's
        # contract. It holds under the settings pinned here; it breaks when the
        # caller's shell changes them -- `-vvv` (documented in AGENTS.md) adds a
        # `task path:` line between banner and status, `minimal` drops banners,
        # arg display rewrites the banner text, and hiding ok hosts removes the
        # status line. Pin them rather than inherit whatever the caller exports.
        env["ANSIBLE_STDOUT_CALLBACK"] = "ansible.builtin.default"
        env["ANSIBLE_VERBOSITY"] = "0"
        env["ANSIBLE_DISPLAY_ARGS_TO_STDOUT"] = "false"
        env["ANSIBLE_DISPLAY_OK_HOSTS"] = "true"
        env["ANSIBLE_NOCOWS"] = "1"
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

    The default stdout callback prints a task's header immediately followed by
    its per-host status line, so demanding ``ok:`` on the line after the header
    rejects a task that was skipped (``skipping:``) as well as a tag selection
    that never emitted the header at all.
    """
    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output

    observation = re.compile(
        rf"^TASK \[{re.escape(task_name)}\] \*+\nok: \[", re.MULTILINE
    )
    assert observation.search(output), (
        f"ansible-playbook exited 0, but the task {task_name!r} was never "
        f"observed completing with 'ok'. Either it did not run (tag selection "
        f"or a skip) or it was renamed.\n{output}"
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
