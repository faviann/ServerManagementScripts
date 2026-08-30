#!/usr/bin/env python3
"""Regression coverage for the managed-host inspection command."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from test_lxc_fleet_preflight import (
    generate_localhost_certificate,
    local_proxmox_server,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
INSPECT = REPO_ROOT / "inspect.sh"
CONTROLLED_API_USER = "inspect-fixture-user@pam"
CONTROLLED_API_TOKEN_ID = "inspect-fixture-token-id"
CONTROLLED_API_TOKEN_SECRET = "inspect-fixture-token-secret"


def run_inspect(
    *arguments: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(INSPECT), *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env or os.environ.copy(),
        timeout=15,
    )


def assert_explicit_operation_and_help() -> None:
    bare = run_inspect()
    if bare.returncode != 2:
        raise AssertionError(f"bare inspect returned {bare.returncode}, not 2")

    help_result = run_inspect("--help")
    help_output = f"{help_result.stdout}\n{help_result.stderr}"
    operations = ("credentials", "connectivity", "containers", "plan")
    if help_result.returncode != 0 or not all(
        operation in help_output for operation in operations
    ):
        raise AssertionError(f"inspect help is incomplete:\n{help_output}")
    if "vars" in help_output:
        raise AssertionError(f"out-of-scope vars operation appeared in help:\n{help_output}")


def controlled_environment(temp_root: Path) -> dict[str, str]:
    home = temp_root / "home"
    bin_dir = temp_root / "bin"
    home.mkdir()
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

Path(os.environ["INSPECT_TEST_CAPTURE"]).write_text(json.dumps({
    "argv": sys.argv[1:],
    "marker": os.environ.get("HOMELAB_IAC_LIFECYCLE_WRAPPER"),
}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "INSPECT_TEST_CAPTURE": str(temp_root / "capture.json"),
        }
    )
    return env


def assert_operations_route_through_shared_live_execution() -> None:
    cases = (
        ("credentials", (), "playbooks/validate-credentials.yml", "localhost"),
        ("connectivity", (), "playbooks/lab-connectivity.yml", "localhost"),
        ("containers", (), "playbooks/proxmox_api_check.yml", "localhost"),
        (
            "plan",
            ("--limit", "target_conflict,release_problem"),
            "playbooks/validate-infrastructure.yml",
            "target_conflict,release_problem",
        ),
    )
    for operation, options, playbook, prerequisite_target in cases:
        with tempfile.TemporaryDirectory(prefix=f"inspect-{operation}-") as temp_dir:
            temp_root = Path(temp_dir)
            env = controlled_environment(temp_root)
            lock_path = Path(env["HOME"]) / ".ansible/homelab-iac-lifecycle.lock"
            lock_path.parent.mkdir(parents=True)
            with lock_path.open("a", encoding="utf-8") as shared_holder:
                fcntl.flock(shared_holder, fcntl.LOCK_SH | fcntl.LOCK_NB)
                result = run_inspect(operation, *options, env=env)
            if result.returncode != 0:
                raise AssertionError(
                    f"{operation} did not coexist with a shared holder:\n"
                    f"{result.stdout}\n{result.stderr}"
                )
            capture = json.loads(
                (temp_root / "capture.json").read_text(encoding="utf-8")
            )
            expected_operation_arguments = [*options]
            if operation == "plan":
                expected_operation_arguments.append("--check")
            expected_arguments = [
                "run",
                "--locked",
                "ansible-playbook",
                playbook,
                *expected_operation_arguments,
                "-e",
                f"prerequisite_target_pattern={prerequisite_target}",
            ]
            if capture != {"argv": expected_arguments, "marker": "1"}:
                raise AssertionError(
                    f"{operation} routed incorrectly:\n"
                    f"expected={expected_arguments!r}\nactual={capture!r}"
                )


def assert_exclusive_contention_names_holder() -> None:
    for operation in ("credentials", "connectivity", "containers", "plan"):
        with tempfile.TemporaryDirectory(prefix=f"inspect-lock-{operation}-") as temp_dir:
            temp_root = Path(temp_dir)
            env = controlled_environment(temp_root)
            lock_path = Path(env["HOME"]) / ".ansible/homelab-iac-lifecycle.lock"
            holder_dir = Path(f"{lock_path}.holders")
            holder_dir.mkdir(parents=True)
            with lock_path.open("a", encoding="utf-8") as exclusive_holder:
                fcntl.flock(exclusive_holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
                (holder_dir / f"{os.getpid()}.holder").write_text(
                    f"pid={os.getpid()} parent_pid={os.getpid()} "
                    "worktree=/controlled/holder\n",
                    encoding="utf-8",
                )
                result = run_inspect(operation, env=env)
            output = f"{result.stdout}\n{result.stderr}"
            if (
                result.returncode != 75
                or f"pid={os.getpid()}" not in output
                or "worktree=/controlled/holder" not in output
                or (temp_root / "capture.json").exists()
            ):
                raise AssertionError(
                    f"{operation} did not fail immediately with holder identity:\n{output}"
                )


def task_names(playbook: str) -> list[str]:
    documents = yaml.safe_load((REPO_ROOT / playbook).read_text(encoding="utf-8"))
    return [
        str(task["name"])
        for document in documents
        for task in document.get("tasks", [])
    ]


def assert_diagnostic_playbooks_are_consolidated() -> None:
    connectivity_source = (REPO_ROOT / "playbooks/lab-connectivity.yml").read_text(
        encoding="utf-8"
    )
    if "Proxmox API" in connectivity_source or "ansible.builtin.uri" in connectivity_source:
        raise AssertionError("connectivity still contains its duplicate Proxmox API play")

    container_tasks = task_names("playbooks/proxmox_api_check.yml")
    expected = ["Query Proxmox API for LXC containers", "List LXC containers"]
    if container_tasks != expected:
        raise AssertionError(
            f"containers playbook is not reduced to the full list: {container_tasks!r}"
        )

    site_documents = yaml.safe_load((REPO_ROOT / "site.yml").read_text(encoding="utf-8"))
    if any(
        document.get("ansible.builtin.import_playbook")
        == "playbooks/validate-infrastructure.yml"
        or "validation" in document.get("tags", [])
        for document in site_documents
    ):
        raise AssertionError("site.yml still exposes standalone validation by tag")

    credentials_source = (
        REPO_ROOT / "playbooks/validate-credentials.yml"
    ).read_text(encoding="utf-8")
    for disclosure in ("API User:", "Token ID:"):
        if disclosure in credentials_source:
            raise AssertionError(f"credential summary still discloses {disclosure}")
    if "API Host:" not in credentials_source:
        raise AssertionError("credential summary lost non-secret endpoint identity")

    standalone_source = (
        REPO_ROOT / "playbooks/tasks/standalone_lifecycle_validation.yml"
    ).read_text(encoding="utf-8")
    if "tasks_from: plan" not in standalone_source or "tasks_from: execute" in standalone_source:
        raise AssertionError("standalone validation is not routed exclusively through planning")

    ssh_bootstrap = yaml.safe_load(
        (
            REPO_ROOT
            / "playbooks/roles/infrastructure/proxmox_host_bootstrap/tasks/ssh_access.yml"
        ).read_text(encoding="utf-8")
    )
    password_install = next(
        task
        for task in ssh_bootstrap
        if task.get("name") == "Configure SSH key authentication (interactive)"
    )
    if "not ansible_check_mode" not in password_install.get("when", []):
        raise AssertionError("plan check mode does not guard password-driven key installation")


def live_fixture_environment(temp_root: Path, inventory_source: str) -> dict[str, str]:
    home = temp_root / "home"
    home.mkdir()
    inventory = temp_root / "inventory.yml"
    inventory.write_text(inventory_source, encoding="utf-8")
    vault_password = temp_root / "vault-pass"
    vault_password.write_text("unused-fixture-placeholder\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "ANSIBLE_INVENTORY": str(inventory),
            "ANSIBLE_VAULT_PASSWORD_FILE": str(vault_password),
        }
    )
    return env


def assert_connectivity_fails_after_reporting_unreachable_targets() -> None:
    with tempfile.TemporaryDirectory(prefix="inspect-connectivity-live-") as temp_dir:
        env = live_fixture_environment(
            Path(temp_dir),
            f"""---
all:
  vars:
    proxmox_api_user: {CONTROLLED_API_USER}
    proxmox_api_token_id: {CONTROLLED_API_TOKEN_ID}
    proxmox_api_token_secret: {CONTROLLED_API_TOKEN_SECRET}
  children:
    lxcs:
      hosts:
        reachable_fixture:
          ansible_connection: local
        unreachable_fixture:
          ansible_connection: ssh
          ansible_host: 127.0.0.1
          ansible_port: 1
          ansible_user: nobody
          ansible_ssh_common_args: -o ConnectTimeout=1 -o BatchMode=yes
""",
        )
        result = run_inspect("connectivity", env=env)
    output = f"{result.stdout}\n{result.stderr}"
    if (
        result.returncode == 0
        or "reachable_fixture" not in output
        or "unreachable_fixture" not in output
        or "SSH unreachable" not in output
    ):
        raise AssertionError(
            "connectivity did not report the complete targeted result and fail:\n"
            f"returncode={result.returncode}\n{output}"
        )
    for credential_value in (
        CONTROLLED_API_USER,
        CONTROLLED_API_TOKEN_ID,
        CONTROLLED_API_TOKEN_SECRET,
    ):
        if credential_value in output:
            raise AssertionError("connectivity disclosed a controlled credential value")


def assert_containers_includes_unreserved_node_container() -> None:
    with tempfile.TemporaryDirectory(prefix="inspect-containers-live-") as temp_dir:
        temp_root = Path(temp_dir)
        certificate = temp_root / "certificate.pem"
        private_key = temp_root / "private-key.pem"
        generate_localhost_certificate(certificate, private_key)
        with local_proxmox_server(certificate, private_key, status=200) as server:
            env = live_fixture_environment(
                temp_root,
                f"""---
all:
  children:
    proxmox_api:
      hosts:
        api_fixture:
          ansible_connection: local
          proxmox_api_host: 127.0.0.1
          proxmox_api_port: {server.server_address[1]}
          proxmox_api_user: {CONTROLLED_API_USER}
          proxmox_api_token_id: {CONTROLLED_API_TOKEN_ID}
          proxmox_api_token_secret: {CONTROLLED_API_TOKEN_SECRET}
          proxmox_default_node: pve-a
          proxmox_verify_ssl: false
    lxcs:
      hosts:
        reserved_fixture:
          vmid: 5101
""",
            )
            result = run_inspect("containers", env=env)
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0 or "5105" not in output or "release-problem" not in output:
        raise AssertionError(f"containers omitted the unreserved node container:\n{output}")
    for credential_value in (
        CONTROLLED_API_USER,
        CONTROLLED_API_TOKEN_ID,
        CONTROLLED_API_TOKEN_SECRET,
    ):
        if credential_value in output:
            raise AssertionError("containers disclosed a controlled credential value")


def assert_credentials_walks_permission_ladder_without_disclosure() -> None:
    with tempfile.TemporaryDirectory(prefix="inspect-credentials-live-") as temp_dir:
        temp_root = Path(temp_dir)
        certificate = temp_root / "certificate.pem"
        private_key = temp_root / "private-key.pem"
        generate_localhost_certificate(certificate, private_key)
        with local_proxmox_server(certificate, private_key, status=200) as server:
            env = live_fixture_environment(
                temp_root,
                f"""---
all:
  children:
    proxmox_api:
      hosts:
        api_fixture:
          ansible_connection: local
          proxmox_api_host: 127.0.0.1
          proxmox_api_port: {server.server_address[1]}
          proxmox_api_user: {CONTROLLED_API_USER}
          proxmox_api_token_id: {CONTROLLED_API_TOKEN_ID}
          proxmox_api_token_secret: {CONTROLLED_API_TOKEN_SECRET}
          proxmox_default_node: pve-a
          proxmox_verify_ssl: false
""",
            )
            result = run_inspect("credentials", env=env)
    output = f"{result.stdout}\n{result.stderr}"
    required_results = (
        "API Connectivity:   PASS",
        "Cluster Access:     PASS",
        "Node Access:        PASS",
        "LXC Access:         PASS",
        "API Host:           127.0.0.1:",
    )
    if result.returncode != 0 or not all(value in output for value in required_results):
        raise AssertionError(f"credentials did not complete its permission ladder:\n{output}")
    for credential_value in (
        CONTROLLED_API_USER,
        CONTROLLED_API_TOKEN_ID,
        CONTROLLED_API_TOKEN_SECRET,
    ):
        if credential_value in output:
            raise AssertionError("credentials disclosed a controlled credential value")


def main() -> int:
    try:
        assert_explicit_operation_and_help()
        assert_operations_route_through_shared_live_execution()
        assert_exclusive_contention_names_holder()
        assert_diagnostic_playbooks_are_consolidated()
        assert_connectivity_fails_after_reporting_unreachable_targets()
        assert_containers_includes_unreserved_node_container()
        assert_credentials_walks_permission_ladder_without_disclosure()
    except (AssertionError, subprocess.TimeoutExpired) as error:
        print(error, file=sys.stderr)
        return 1
    print("ok: inspect command exposes read-only managed-host diagnostics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
