#!/usr/bin/env python3
"""Grammar and boundary coverage for the workstation setup command."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ansible_test_helper import ansible_playbook_command


REPO_ROOT = Path(__file__).resolve().parents[2]

# Every external program the command may reach for. A fake stands in for each
# one so the recorded child-process boundary is the whole boundary: anything
# the command invokes is either recorded here or absent from PATH.
RECORDED_EXECUTABLES = (
    "uv",
    "curl",
    "dpkg",
    "sudo",
    "apt",
    "apt-get",
    "ansible",
    "ansible-playbook",
    "ansible-galaxy",
    "ssh",
    "sshpass",
    "ssh-keygen",
)

RECORDING_SHIM = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
with Path(os.environ["SETUP_TEST_CAPTURE"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps({"name": name, "argv": sys.argv[1:]}) + "\\n")
if name == "dpkg":
    # Guided setup reads `dpkg -l` to decide whether sshpass is installed.
    print("ii  sshpass  1.09  amd64  Non-interactive ssh password provider")
"""


def setup_project(tmp_path: Path, *, locked_environment: bool = True) -> Path:
    """A throwaway project root the real command runs against.

    `setup.sh` resolves its project root from its own path, so the command is
    reached through a symlink rather than at the repository root. That keeps
    guided setup's filesystem writes inside the test and lets a case decide
    whether the locked environment is present.
    """
    project = tmp_path / "project"
    project.mkdir()
    for command in ("setup.sh", "vault.sh"):
        (project / command).symlink_to(REPO_ROOT / command)
    (project / "bootstrap.yml").write_text("---\n", encoding="utf-8")
    (project / "inventory" / "group_vars" / "all").mkdir(parents=True)
    (project / ".agents" / "skills" / "example-skill").mkdir(parents=True)
    if locked_environment:
        (project / ".venv").mkdir()
    return project


def setup_environment(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in RECORDED_EXECUTABLES:
        fake = bin_dir / name
        fake.write_text(RECORDING_SHIM, encoding="utf-8")
        fake.chmod(0o755)

    home = tmp_path / "home"
    (home / ".ansible").mkdir(parents=True)
    (home / ".ansible" / "vault-pass").write_text("passphrase\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "SETUP_TEST_CAPTURE": str(tmp_path / "commands.jsonl"),
        }
    )
    return env


def run_setup(
    project: Path,
    env: dict[str, str],
    *arguments: str,
    stdin: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(project / "setup.sh"), *arguments],
        cwd=project,
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
        timeout=60,
    )


def captured_commands(env: dict[str, str]) -> list[dict[str, object]]:
    capture = Path(env["SETUP_TEST_CAPTURE"])
    if not capture.exists():
        return []
    return [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]


# --- AC8: usage grammar and exit statuses ----------------------------------


def test_help_exits_zero_and_names_the_operations(tmp_path: Path) -> None:
    project = setup_project(tmp_path)
    env = setup_environment(tmp_path)

    result = run_setup(project, env, "--help")

    assert result.returncode == 0, result.stderr
    output = f"{result.stdout}\n{result.stderr}"
    assert "sync" in output
    assert "bootstrap" in output
    assert captured_commands(env) == []


@pytest.mark.parametrize(
    "arguments",
    [("bogus",), ("--bogus",), ("sync", "--bogus"), ("bootstrap", "--bogus")],
)
def test_unknown_operation_or_option_is_invalid_usage(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    project = setup_project(tmp_path)
    env = setup_environment(tmp_path)

    result = run_setup(project, env, *arguments)

    assert result.returncode == 2, result.stdout
    assert captured_commands(env) == []


# --- AC2 / AC3: the agent-safe operations ----------------------------------


def assert_no_managed_host_contact(commands: list[dict[str, object]]) -> None:
    """No child reaches off the control node.

    Only the packaging tool may be invoked, and the only Ansible run it may
    carry is the local control-node bootstrap play.
    """
    for entry in commands:
        assert entry["name"] == "uv", entry
        argv = entry["argv"]
        if "ansible-playbook" in argv or "ansible" in argv:
            assert argv == ["run", "--locked", "ansible-playbook", "bootstrap.yml"]


def test_sync_performs_locked_synchronization_only(tmp_path: Path) -> None:
    project = setup_project(tmp_path)
    env = setup_environment(tmp_path)

    result = run_setup(project, env, "sync")

    assert result.returncode == 0, result.stderr
    commands = captured_commands(env)
    assert [(entry["name"], entry["argv"]) for entry in commands] == [
        ("uv", ["sync", "--locked"])
    ]
    assert_no_managed_host_contact(commands)


def test_bootstrap_reconciles_through_the_control_node_playbook(
    tmp_path: Path,
) -> None:
    project = setup_project(tmp_path)
    env = setup_environment(tmp_path)

    result = run_setup(project, env, "bootstrap")

    assert result.returncode == 0, result.stderr
    commands = captured_commands(env)
    assert [(entry["name"], entry["argv"]) for entry in commands] == [
        ("uv", ["run", "--locked", "ansible-playbook", "bootstrap.yml"])
    ]
    assert_no_managed_host_contact(commands)


def test_bootstrap_without_a_locked_environment_directs_the_caller_to_sync(
    tmp_path: Path,
) -> None:
    project = setup_project(tmp_path, locked_environment=False)
    env = setup_environment(tmp_path)

    result = run_setup(project, env, "bootstrap")

    assert result.returncode != 0
    assert "./setup.sh sync" in f"{result.stdout}\n{result.stderr}"
    assert captured_commands(env) == []


# --- AC4: bootstrap never replaces an existing controller SSH key ----------


def test_bootstrap_delegates_key_creation_instead_of_generating_a_key(
    tmp_path: Path,
) -> None:
    project = setup_project(tmp_path)
    env = setup_environment(tmp_path)

    result = run_setup(project, env, "bootstrap")

    assert result.returncode == 0, result.stderr
    assert "ssh-keygen" not in {entry["name"] for entry in captured_commands(env)}
    assert not (Path(env["HOME"]) / ".ansible" / "ssh" / "proxmox_lxc").exists()


# --- AC5: both operations are independently runnable and idempotent --------


@pytest.mark.parametrize("operation", ["sync", "bootstrap"])
def test_each_operation_runs_alone_and_repeats_identically(
    tmp_path: Path, operation: str
) -> None:
    project = setup_project(tmp_path)
    env = setup_environment(tmp_path)

    first = run_setup(project, env, operation)
    commands_after_first = captured_commands(env)
    second = run_setup(project, env, operation)
    commands_after_second = captured_commands(env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert second.stdout == first.stdout
    assert commands_after_second == commands_after_first * 2


CONTROL_NODE_BOOTSTRAP_PLAY = (
    REPO_ROOT / "tests" / "regression" / "fixtures" / "control_node_bootstrap_test.yml"
)


def run_control_node_bootstrap(
    tmp_path: Path, home: Path
) -> subprocess.CompletedProcess[str]:
    """Run the reconciler `./setup.sh bootstrap` delegates to, offline.

    Collection and role installation is the one part that would reach the
    network, so a fake ansible-galaxy stands in for it. Nothing else in the
    role leaves the control node.
    """
    fake_galaxy = tmp_path / "ansible-galaxy"
    fake_galaxy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_galaxy.chmod(0o755)

    project_root = tmp_path / "bootstrap-project"
    (project_root / "collections").mkdir(parents=True, exist_ok=True)
    (project_root / "collections" / "requirements.yml").write_text(
        "collections: []\n", encoding="utf-8"
    )

    return subprocess.run(
        [
            *ansible_playbook_command(supplies_own_inventory=True),
            "-i",
            "localhost,",
            "-c",
            "local",
            str(CONTROL_NODE_BOOTSTRAP_PLAY),
            "-e",
            json.dumps(
                {
                    "control_node_project_root": str(project_root),
                    "control_node_home_dir": str(home),
                    "control_node_vault_password_file": str(
                        home / ".ansible" / "vault-pass"
                    ),
                    "control_node_skip_system_packages": True,
                    "control_node_ansible_galaxy_executable": str(fake_galaxy),
                }
            ),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=300,
    )


def test_reconciliation_creates_the_controller_key_only_when_absent(
    tmp_path: Path,
) -> None:
    env = setup_environment(tmp_path)
    home = Path(env["HOME"])
    private_key = home / ".ansible" / "ssh" / "proxmox_lxc"
    public_key = home / ".ansible" / "ssh" / "proxmox_lxc.pub"

    absent = run_control_node_bootstrap(tmp_path, home)

    assert absent.returncode == 0, absent.stdout + absent.stderr
    assert private_key.exists() and public_key.exists()
    existing = (private_key.read_bytes(), public_key.read_bytes())

    present = run_control_node_bootstrap(tmp_path, home)

    assert present.returncode == 0, present.stdout + present.stderr
    assert (private_key.read_bytes(), public_key.read_bytes()) == existing


# --- AC1 / AC6 / AC7: guided workstation setup -----------------------------

ENCRYPTED_VAULT = "$ANSIBLE_VAULT;1.1;AES256\n3132330a\n"


def vault_file(project: Path) -> Path:
    return project / "inventory" / "group_vars" / "all" / "vault.yml"


def test_no_argument_run_is_still_guided_workstation_setup(tmp_path: Path) -> None:
    project = setup_project(tmp_path)
    vault_file(project).write_text(ENCRYPTED_VAULT, encoding="utf-8")
    env = setup_environment(tmp_path)

    result = run_setup(project, env, stdin="n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Controller Setup" in result.stdout
    # Guided setup, unlike either operation, checks workstation prerequisites.
    assert "dpkg" in {entry["name"] for entry in captured_commands(env)}


def test_guided_setup_leaves_an_existing_encrypted_vault_untouched(
    tmp_path: Path,
) -> None:
    project = setup_project(tmp_path)
    vault_file(project).write_text(ENCRYPTED_VAULT, encoding="utf-8")
    env = setup_environment(tmp_path)

    result = run_setup(project, env, stdin="n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert vault_file(project).read_text(encoding="utf-8") == ENCRYPTED_VAULT
    # Nothing was offered and nothing ran: no prompt, and no ./vault.sh child.
    assert "Set up Proxmox API credentials now" not in result.stdout
    assert "configure: FAIL" not in result.stderr


def test_guided_setup_offers_the_vault_commands_guided_configuration(
    tmp_path: Path,
) -> None:
    project = setup_project(tmp_path)
    env = setup_environment(tmp_path)

    declined = run_setup(project, env, stdin="n\n")

    assert declined.returncode == 0, declined.stdout + declined.stderr
    assert "./vault.sh configure" in declined.stdout
    assert "configure: FAIL" not in declined.stderr

    accepted = run_setup(project, env, stdin="y\n")

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    # ./vault.sh reports a refused non-interactive `configure` this way, so the
    # line is evidence that guided setup handed the vault step to that command.
    assert "configure: FAIL" in accepted.stderr


def test_guided_setup_names_only_supported_commands(tmp_path: Path) -> None:
    project = setup_project(tmp_path)
    vault_file(project).write_text(ENCRYPTED_VAULT, encoding="utf-8")
    env = setup_environment(tmp_path)

    result = run_setup(project, env, stdin="n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    output = f"{result.stdout}\n{result.stderr}"
    assert "configure-vault.sh" not in output
    assert "rotate-vault-passphrase.sh" not in output
    for named in ("./setup.sh sync", "./setup.sh bootstrap", "./vault.sh"):
        assert named in output
