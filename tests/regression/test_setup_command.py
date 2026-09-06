#!/usr/bin/env python3
"""Regression coverage for the ./setup.sh command facade.

`setup.sh` is a facade: it dispatches operations, delegates the work to `uv`
and to the tracked bootstrap play, and reconciles nothing itself. These tests
observe exactly that boundary -- the process, its exit status, its output, and
the child commands it invokes.

What the bootstrap play then does -- installing collections, reconciling
external role pins, creating the controller SSH key without replacing an
existing one -- is owned by `test_control_node_dependencies.py`, which drives
the role directly. Re-proving it here would mean rebuilding an Ansible
environment around a shell wrapper.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

# The programs setup.sh can reach. Shimming them keeps guided setup off the
# package manager and off the network, and records what was invoked. This is a
# sandbox, not a proof: the assertions below name the exact commands each
# operation is allowed to run, which is the claim worth making.
SHIMMED = ("uv", "curl", "dpkg", "sudo", "apt", "apt-get")

RECORDING_SHIM = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

name = Path(sys.argv[0]).name
with Path(os.environ["SETUP_TEST_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps([name, *sys.argv[1:]]) + "\\n")
if name == "dpkg":
    # Guided setup reads `dpkg -l` to decide whether sshpass is installed.
    print("ii  sshpass  1.09  amd64  Non-interactive ssh password provider")
raise SystemExit(int(os.environ.get("SETUP_TEST_CHILD_STATUS", "0")))
'''

SYNC = ["uv", "sync", "--locked"]
BOOTSTRAP = ["uv", "run", "--no-sync", "--locked", "ansible-playbook", "bootstrap.yml"]

ENCRYPTED_VAULT = "$ANSIBLE_VAULT;1.1;AES256\n3132330a\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A throwaway project root reached through a symlink.

    `setup.sh` resolves its project root from its own path, so this keeps the
    guided path's filesystem writes inside the test.
    """
    project = tmp_path / "project"
    (project / "inventory" / "group_vars" / "all").mkdir(parents=True)
    (project / ".agents" / "skills" / "example-skill").mkdir(parents=True)
    for name in ("setup.sh", "vault.sh"):
        (project / name).symlink_to(REPO_ROOT / name)
    (project / "bootstrap.yml").write_text("---\n", encoding="utf-8")
    (project / ".venv").mkdir()
    return project


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in SHIMMED:
        shim = bin_dir / name
        shim.write_text(RECORDING_SHIM, encoding="utf-8")
        shim.chmod(0o755)

    home = tmp_path / "home"
    (home / ".ansible").mkdir(parents=True)
    (home / ".ansible" / "vault-pass").write_text("passphrase\n", encoding="utf-8")

    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "SETUP_TEST_LOG": str(tmp_path / "children.jsonl"),
        }
    )
    return environment


def run(
    project: Path, env: dict[str, str], *arguments: str, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(project / "setup.sh"), *arguments],
        cwd=project,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )


def children(env: dict[str, str]) -> list[list[str]]:
    log = Path(env["SETUP_TEST_LOG"])
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def vault_file(project: Path) -> Path:
    return project / "inventory" / "group_vars" / "all" / "vault.yml"


# --- operations -----------------------------------------------------------


def test_sync_runs_locked_synchronization_and_nothing_else(project, env) -> None:
    result = run(project, env, "sync")

    assert result.returncode == 0, result.stderr
    assert children(env) == [SYNC]


def test_bootstrap_runs_the_tracked_play_and_nothing_else(project, env) -> None:
    result = run(project, env, "bootstrap")

    assert result.returncode == 0, result.stderr
    assert children(env) == [BOOTSTRAP]


def test_bootstrap_without_a_locked_environment_directs_the_caller_to_sync(
    project, env
) -> None:
    (project / ".venv").rmdir()

    result = run(project, env, "bootstrap")

    assert result.returncode != 0
    assert "./setup.sh sync" in f"{result.stdout}\n{result.stderr}"
    assert children(env) == []


def test_sync_without_the_packaging_tool_fails_on_the_documented_status(
    project, env, tmp_path
) -> None:
    (tmp_path / "bin" / "uv").unlink()
    # PATH still carries /usr/bin, so this case only means anything while no
    # real uv is reachable there.
    assert shutil.which("uv", path=env["PATH"]) is None

    result = run(project, env, "sync")

    assert result.returncode == 1
    assert "uv" in f"{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize("operation", ["sync", "bootstrap"])
def test_child_failure_is_not_reported_as_success(project, env, operation) -> None:
    env["SETUP_TEST_CHILD_STATUS"] = "9"

    result = run(project, env, operation)

    assert result.returncode == 1, result.stdout


@pytest.mark.parametrize(
    ("operation", "expected"), [("sync", SYNC), ("bootstrap", BOOTSTRAP)]
)
def test_operations_are_independently_repeatable(
    project, env, operation, expected
) -> None:
    first = run(project, env, operation)
    second = run(project, env, operation)

    assert (first.returncode, second.returncode) == (0, 0), first.stderr
    assert second.stdout == first.stdout
    assert children(env) == [expected, expected]


# --- grammar --------------------------------------------------------------


def test_help_exits_zero_and_names_the_operations(project, env) -> None:
    result = run(project, env, "--help")

    assert result.returncode == 0
    assert "sync" in result.stdout and "bootstrap" in result.stdout
    assert children(env) == []


@pytest.mark.parametrize(
    "arguments",
    [
        ("bogus",),
        ("--bogus",),
        ("sync", "extra"),
        ("bootstrap", "extra"),
        ("--help", "extra"),
    ],
)
def test_unknown_or_surplus_input_is_invalid_usage(project, env, arguments) -> None:
    result = run(project, env, *arguments)

    assert result.returncode == 2
    assert result.stderr.startswith("setup.sh: ")
    assert children(env) == []


# --- guided setup ---------------------------------------------------------


def test_no_arguments_runs_guided_workstation_setup(project, env) -> None:
    vault_file(project).write_text(ENCRYPTED_VAULT, encoding="utf-8")

    result = run(project, env, stdin="n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Controller Setup" in result.stdout
    # Guided setup, unlike either operation, checks workstation prerequisites
    # and then reuses both operations rather than duplicating them.
    invoked = children(env)
    assert ["dpkg", "-l"] in invoked
    assert SYNC in invoked and BOOTSTRAP in invoked


def test_guided_setup_leaves_an_existing_encrypted_vault_untouched(
    project, env
) -> None:
    vault_file(project).write_text(ENCRYPTED_VAULT, encoding="utf-8")

    result = run(project, env, stdin="n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert vault_file(project).read_text(encoding="utf-8") == ENCRYPTED_VAULT
    assert "Set up Proxmox API credentials now" not in result.stdout


def test_guided_setup_delegates_configuration_to_the_vault_command(
    project, env
) -> None:
    # An unencrypted vault takes the same offer, with a warning: setup.sh does
    # not convert it, ./vault.sh configure owns that prompt.
    vault_file(project).write_text("vault_proxmox_api_user: plain\n", encoding="utf-8")

    declined = run(project, env, stdin="n\n")

    assert declined.returncode == 0, declined.stderr
    assert "NOT encrypted" in declined.stdout
    assert "./vault.sh configure" in declined.stdout

    accepted = run(project, env, stdin="y\n")

    # ./vault.sh reports a refused non-interactive `configure` this way, so the
    # line is evidence that guided setup handed the step to that command.
    assert "configure: FAIL" in accepted.stderr


def test_guided_setup_names_only_supported_commands(project, env) -> None:
    vault_file(project).write_text(ENCRYPTED_VAULT, encoding="utf-8")

    result = run(project, env, stdin="n\n")

    output = f"{result.stdout}\n{result.stderr}"
    assert "configure-vault.sh" not in output
    assert "rotate-vault-passphrase.sh" not in output
    for supported in ("./setup.sh sync", "./setup.sh bootstrap", "./vault.sh"):
        assert supported in output
