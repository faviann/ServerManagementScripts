#!/usr/bin/env python3
"""Grammar and boundary coverage for the workstation setup command."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

# Programs that would carry the command off the control node, plus the ones it
# legitimately uses. A fake stands in for each, so reaching for any of them is
# recorded rather than silent. PATH also carries /usr/bin and /bin, because the
# command needs ordinary utilities; the guarantee is therefore that none of
# these transports was used, not that no program at all could have been.
RECORDED_EXECUTABLES = (
    # The packaging tool, and the prerequisite programs guided setup uses.
    "uv",
    "curl",
    "dpkg",
    # OS package installation.
    "sudo",
    "apt",
    "apt-get",
    "aptitude",
    # Ansible, which is how this repository reaches a managed host.
    "ansible",
    "ansible-playbook",
    "ansible-galaxy",
    "ansible-connection",
    "ansible-pull",
    "ansible-console",
    # Remote transports and network clients.
    "ssh",
    "scp",
    "sftp",
    "rsync",
    "sshpass",
    "ssh-keygen",
    "wget",
    "nc",
    "ncat",
    "socat",
    "telnet",
    "git",
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
    ("arguments", "reported"),
    [
        (("bogus",), "unknown operation"),
        (("--bogus",), "unknown option"),
        # An operation that takes no arguments has no options to be unknown,
        # so anything after it is reported as the surplus it is.
        (("sync", "--bogus"), "sync takes no arguments"),
        (("bootstrap", "--bogus"), "bootstrap takes no arguments"),
        (("sync", "extra"), "sync takes no arguments"),
        (("bootstrap", "extra"), "bootstrap takes no arguments"),
        (("--help", "extra"), "--help takes no arguments"),
    ],
)
def test_unknown_or_surplus_input_is_invalid_usage(
    tmp_path: Path, arguments: tuple[str, ...], reported: str
) -> None:
    project = setup_project(tmp_path)
    env = setup_environment(tmp_path)

    result = run_setup(project, env, *arguments)

    assert result.returncode == 2, result.stdout
    assert f"setup.sh: {reported}" in result.stderr
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


def test_sync_without_the_packaging_tool_fails_on_the_documented_status(
    tmp_path: Path,
) -> None:
    project = setup_project(tmp_path)
    env = setup_environment(tmp_path)
    (tmp_path / "bin" / "uv").unlink()

    result = run_setup(project, env, "sync")

    # 127 from an unguarded `command not found` is outside the exit convention.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "uv" in f"{result.stdout}\n{result.stderr}"
    assert captured_commands(env) == []


@pytest.mark.parametrize("operation", ["sync", "bootstrap"])
def test_a_failing_operation_reports_failure_rather_than_success(
    tmp_path: Path, operation: str
) -> None:
    project = setup_project(tmp_path)
    env = setup_environment(tmp_path)
    failing_uv = tmp_path / "bin" / "uv"
    failing_uv.write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
    failing_uv.chmod(0o755)

    result = run_setup(project, env, operation)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "synchronized" not in result.stdout
    assert "reconciled" not in result.stdout


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


# The real reconciliation the command delegates to, reached at the public
# boundary. The throwaway project root borrows everything `uv run --locked`
# and ansible.cfg need from the repository, and carries its own bootstrap.yml
# applying the real role with test paths. Only ansible-galaxy is faked -- it is
# the one step that would leave the machine.
BORROWED_FROM_REPOSITORY = (
    "setup.sh",
    "vault.sh",
    "pyproject.toml",
    "uv.lock",
    "ansible.cfg",
    "playbooks",
    "library",
    ".venv",
)

RECONCILING_BOOTSTRAP_PLAY = """---
- name: Bootstrap Ansible control node
  hosts: localhost
  connection: local
  gather_facts: true
  become: false
  vars:
    control_node_project_root: "{{ playbook_dir }}"
    control_node_collection_requirements: >-
      {{ [playbook_dir, 'collections', 'requirements.yml'] | path_join }}
    control_node_ansible_galaxy_executable: "{{ playbook_dir }}/fake-ansible-galaxy"
  roles:
    - base/control_node_bootstrap
"""


def reconciling_project(tmp_path: Path) -> Path:
    project = tmp_path / "reconciling-project"
    project.mkdir()
    for name in BORROWED_FROM_REPOSITORY:
        (project / name).symlink_to(REPO_ROOT / name)
    (project / "collections").mkdir()
    (project / "collections" / "requirements.yml").write_text(
        "collections: []\n", encoding="utf-8"
    )
    (project / "inventory" / "group_vars" / "all").mkdir(parents=True)
    (project / "bootstrap.yml").write_text(RECONCILING_BOOTSTRAP_PLAY, encoding="utf-8")
    galaxy = project / "fake-ansible-galaxy"
    galaxy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    galaxy.chmod(0o755)
    return project


def reconciling_environment(tmp_path: Path) -> dict[str, str]:
    """The real toolchain, but a private HOME the controller key lands in."""
    home = tmp_path / "reconciling-home"
    (home / ".ansible").mkdir(parents=True)
    (home / ".ansible" / "vault-pass").write_text("passphrase\n", encoding="utf-8")
    env = os.environ.copy()
    env["HOME"] = str(home)
    return env


def test_bootstrap_creates_the_controller_key_only_when_absent(
    tmp_path: Path,
) -> None:
    project = reconciling_project(tmp_path)
    env = reconciling_environment(tmp_path)
    private_key = Path(env["HOME"]) / ".ansible" / "ssh" / "proxmox_lxc"
    public_key = Path(env["HOME"]) / ".ansible" / "ssh" / "proxmox_lxc.pub"
    assert not private_key.exists()

    absent = run_setup(project, env, "bootstrap")

    assert absent.returncode == 0, absent.stdout + absent.stderr
    assert private_key.exists() and public_key.exists()
    existing = (private_key.read_bytes(), public_key.read_bytes())

    present = run_setup(project, env, "bootstrap")

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
    # The vault step reported the encrypted file and stopped there: no offer
    # was printed, so nothing could accept one. (An accepted offer is observed
    # separately, in the missing-vault case below.)
    assert "leaving it untouched" in result.stdout
    assert "Set up Proxmox API credentials now" not in result.stdout


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


def test_guided_setup_warns_about_an_unencrypted_vault(tmp_path: Path) -> None:
    project = setup_project(tmp_path)
    plaintext = "---\nvault_proxmox_api_user: root@pam\n"
    vault_file(project).write_text(plaintext, encoding="utf-8")
    env = setup_environment(tmp_path)

    result = run_setup(project, env, stdin="n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    output = f"{result.stdout}\n{result.stderr}"
    assert "NOT encrypted" in output
    assert "./vault.sh configure" in output
    # setup.sh must not convert it itself; ./vault.sh owns that prompt.
    assert vault_file(project).read_text(encoding="utf-8") == plaintext


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
