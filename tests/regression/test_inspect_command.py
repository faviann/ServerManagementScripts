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
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

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
    *arguments: str,
    env: dict[str, str] | None = None,
    timeout: int = 15,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(INSPECT), *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env or os.environ.copy(),
        timeout=timeout,
    )


def assert_explicit_operation_and_help() -> None:
    bare = run_inspect()
    if bare.returncode != 2:
        raise AssertionError(f"bare inspect returned {bare.returncode}, not 2")

    help_result = run_inspect("--help")
    help_output = f"{help_result.stdout}\n{help_result.stderr}"
    operations = ("credentials", "connectivity", "containers", "plan", "vars")
    if help_result.returncode != 0 or not all(
        operation in help_output for operation in operations
    ):
        raise AssertionError(f"inspect help is incomplete:\n{help_output}")


def vars_environment(
    temp_root: Path,
    inventory_output: str,
    *,
    inventory_stderr: str = "",
    inventory_status: int = 0,
) -> dict[str, str]:
    env = controlled_environment(temp_root)
    fake_uv = Path(env["PATH"].split(":", 1)[0]) / "uv"
    fake_uv.write_text(
        f"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

capture_dir = Path(f"{{os.environ['INSPECT_TEST_CAPTURE']}}.invocations")
capture_dir.mkdir(exist_ok=True)
capture = capture_dir / f"{{os.getpid()}}.json"
capture.write_text(json.dumps({{
    "argv": sys.argv[1:],
    "marker": os.environ.get("HOMELAB_IAC_LIFECYCLE_WRAPPER"),
}}), encoding="utf-8")
if "ansible-inventory" in sys.argv:
    sys.stdout.write({inventory_output!r})
    sys.stderr.write({inventory_stderr!r})
    raise SystemExit({inventory_status})
elif "python" in sys.argv:
    python_at = sys.argv.index("python")
    os.execv(sys.executable, [sys.executable, *sys.argv[python_at + 1:]])
""",
        encoding="utf-8",
    )
    return env


def vars_invocations(temp_root: Path) -> list[dict[str, object]]:
    capture_dir = temp_root / "capture.json.invocations"
    return sorted(
        (
            json.loads(capture_path.read_text(encoding="utf-8"))
            for capture_path in capture_dir.glob("*.json")
        ),
        key=lambda capture: capture["argv"],
    )


def expected_host_vars_invocations(host: str) -> list[dict[str, object]]:
    return [
        {
            "argv": [
                "run",
                "--locked",
                "ansible-inventory",
                "-i",
                "inventory/hosts.yml",
                "--host",
                host,
                "--yaml",
            ],
            "marker": None,
        },
        {
            "argv": ["run", "--locked", "python", "-m", "scripts.masked_inventory"],
            "marker": None,
        },
    ]


def expected_graph_vars_invocations() -> list[dict[str, object]]:
    return [
        {
            "argv": [
                "run",
                "--locked",
                "ansible-inventory",
                "-i",
                "inventory/hosts.yml",
                "--graph",
            ],
            "marker": None,
        }
    ]


def assert_vars_masks_vault_derived_values_without_live_execution() -> None:
    fixture_secret = "SeCrEt42"
    diagnostic_secret = "DiagnosticSecret42"
    inventory_output = f"""---
ordinary_value: visible
vault_feature_enabled: "true"
vault_primary_secret: {fixture_secret}
derived_header: Bearer {fixture_secret}
vault_numeric_secret: 12345678
derived_numeric_value: token=12345678
derived_mapping:
  authorization: Bearer {fixture_secret}
  mode: fixture
derived_sequence:
  - prefix
  - {fixture_secret}
  - 7
"""
    with tempfile.TemporaryDirectory(prefix="inspect-vars-") as temp_dir:
        temp_root = Path(temp_dir)
        env = vars_environment(
            temp_root,
            inventory_output,
            inventory_stderr=f"inventory warning with {diagnostic_secret}\n",
        )
        lock_path = Path(env["HOME"]) / ".ansible/homelab-iac-lifecycle.lock"
        lock_path.parent.mkdir(parents=True)
        with lock_path.open("a", encoding="utf-8") as exclusive_holder:
            fcntl.flock(exclusive_holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run_inspect("vars", "fixture_host", env=env)

        captures = vars_invocations(temp_root)

    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        raise AssertionError(
            f"vars did not preserve inventory success: {result.returncode}\n{output}"
        )
    if any(
        secret in output
        for secret in (diagnostic_secret, fixture_secret, "12345678")
    ):
        raise AssertionError(f"vars disclosed a fixture secret:\n{output}")
    rendered_inventory = yaml.safe_load(result.stdout)
    expected_composites = {
        "derived_mapping": (
            "{'aaaaaaaaaaaaa':·'aaaaaa·aaaaaa99',·'aaaa':·'aaaaaaa'}"
        ),
        "derived_sequence": "['aaaaaa',·'aaaaaa99',·9]",
    }
    if {
        key: rendered_inventory.get(key) for key in expected_composites
    } != expected_composites:
        raise AssertionError(
            "vars did not mask each containing composite as one whole value:\n"
            f"{result.stdout}"
        )
    for expected in (
        "ordinary_value: visible",
        "vault_feature_enabled: aaaa",
        "aaaaaa·aaaaaa99",
        "vault_numeric_secret: '99999999'",
        "derived_numeric_value: aaaaa=99999999",
    ):
        if expected not in output:
            raise AssertionError(f"vars omitted expected masked inventory {expected!r}:\n{output}")
    expected_captures = expected_host_vars_invocations("fixture_host")
    if captures != expected_captures:
        raise AssertionError(
            f"vars used a live or unexpected execution path: {captures!r}"
        )


def assert_vars_normalizes_inventory_failure_without_disclosure() -> None:
    fixture_secret = "FailureSecret42"
    diagnostic_secret = "FailureDiagnosticSecret42"
    inventory_output = f"""---
ordinary_value: visible
vault_failure_secret: {fixture_secret}
"""
    with tempfile.TemporaryDirectory(prefix="inspect-vars-failure-") as temp_dir:
        temp_root = Path(temp_dir)
        env = vars_environment(
            temp_root,
            inventory_output,
            inventory_stderr=f"inventory failed with {diagnostic_secret}\n",
            inventory_status=23,
        )
        result = run_inspect("vars", "fixture_host", env=env)
        captures = vars_invocations(temp_root)

    output = f"{result.stdout}\n{result.stderr}"
    if (
        result.returncode != 1
        or "ordinary_value: visible" not in result.stdout
        or fixture_secret in output
        or diagnostic_secret in output
    ):
        raise AssertionError(
            f"vars did not safely normalize inventory failure:\n{output}"
        )
    expected_captures = expected_host_vars_invocations("fixture_host")
    if captures != expected_captures:
        raise AssertionError(
            f"failing vars used a live or unexpected execution path: {captures!r}"
        )


def assert_vars_content_rule_ignores_seven_character_vault_values() -> None:
    inventory_output = """---
vault_short_value: Abc1234
derived_short_value: prefix-Abc1234
"""
    with tempfile.TemporaryDirectory(prefix="inspect-vars-short-") as temp_dir:
        env = vars_environment(Path(temp_dir), inventory_output)
        result = run_inspect("vars", "fixture_host", env=env)
    if result.returncode != 0 or "derived_short_value: prefix-Abc1234" not in result.stdout:
        raise AssertionError(
            "vars selected a derived value containing only a seven-character vault value:\n"
            f"{result.stdout}\n{result.stderr}"
        )


def assert_vars_graph_preserves_inventory_tree_without_live_execution() -> None:
    graph_output = "@all:\n  |--@lxcs:\n  |  |--fixture_host\n"
    diagnostic_secret = "GraphDiagnosticSecret42"
    with tempfile.TemporaryDirectory(prefix="inspect-vars-graph-") as temp_dir:
        temp_root = Path(temp_dir)
        env = vars_environment(
            temp_root,
            graph_output,
            inventory_stderr=f"inventory warning with {diagnostic_secret}\n",
        )
        lock_path = Path(env["HOME"]) / ".ansible/homelab-iac-lifecycle.lock"
        lock_path.parent.mkdir(parents=True)
        with lock_path.open("a", encoding="utf-8") as exclusive_holder:
            fcntl.flock(exclusive_holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run_inspect("vars", "--graph", env=env)
        captures = vars_invocations(temp_root)

    output = f"{result.stdout}\n{result.stderr}"
    if (
        result.returncode != 0
        or result.stdout != graph_output
        or diagnostic_secret in output
    ):
        raise AssertionError(f"vars --graph did not preserve the inventory tree:\n{result.stdout}\n{result.stderr}")
    expected_captures = expected_graph_vars_invocations()
    if captures != expected_captures:
        raise AssertionError(
            f"vars --graph used a live or unexpected execution path: {captures!r}"
        )


def assert_vars_graph_normalizes_inventory_failure_without_disclosure() -> None:
    graph_output = "@all:\n  |--@lxcs:\n  |  |--fixture_host\n"
    diagnostic_secret = "GraphFailureDiagnosticSecret42"
    with tempfile.TemporaryDirectory(prefix="inspect-vars-graph-failure-") as temp_dir:
        temp_root = Path(temp_dir)
        env = vars_environment(
            temp_root,
            graph_output,
            inventory_stderr=f"inventory failed with {diagnostic_secret}\n",
            inventory_status=24,
        )
        result = run_inspect("vars", "--graph", env=env)
        captures = vars_invocations(temp_root)

    output = f"{result.stdout}\n{result.stderr}"
    if (
        result.returncode != 1
        or result.stdout != graph_output
        or diagnostic_secret in output
    ):
        raise AssertionError(
            f"vars --graph did not safely normalize inventory failure:\n{output}"
        )
    expected_captures = expected_graph_vars_invocations()
    if captures != expected_captures:
        raise AssertionError(
            "failing vars --graph used a live or unexpected execution path: "
            f"{captures!r}"
        )


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


def assert_public_plan_reports_all_problems_without_disclosure_or_mutation() -> None:
    with tempfile.TemporaryDirectory(prefix="inspect-plan-live-") as temp_dir:
        temp_root = Path(temp_dir)
        certificate = temp_root / "certificate.pem"
        private_key = temp_root / "private-key.pem"
        ssh_private_key = temp_root / "fixture-ssh-key"
        ssh_public_key = temp_root / "fixture-ssh-key.pub"
        generate_localhost_certificate(certificate, private_key)
        ssh_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ssh_private_key.write_bytes(
            ssh_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.OpenSSH,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        ssh_private_key.chmod(0o600)
        ssh_public_key.write_bytes(
            ssh_key.public_key().public_bytes(
                encoding=serialization.Encoding.OpenSSH,
                format=serialization.PublicFormat.OpenSSH,
            )
            + b" inspect@test\n"
        )
        inventory = yaml.safe_load(
            (
                REPO_ROOT
                / "tests/regression/fixtures/lxc_fleet_preflight_inventory.yml"
            ).read_text(encoding="utf-8")
        )
        with local_proxmox_server(certificate, private_key, status=200) as api_server:
            inventory["all"]["vars"] = {
                "proxmox_api_host": "127.0.0.1",
                "proxmox_api_port": api_server.server_address[1],
                "proxmox_api_user": CONTROLLED_API_USER,
                "proxmox_api_token_id": CONTROLLED_API_TOKEN_ID,
                "proxmox_api_token_secret": CONTROLLED_API_TOKEN_SECRET,
                "proxmox_default_node": "pve-a",
                "proxmox_verify_ssl": False,
                "proxmox_host": "controlled.invalid",
                "proxmox_ssh_port": 1,
                "proxmox_ssh_connect_timeout": 1,
                "proxmox_ssh_key_private": str(ssh_private_key),
                "proxmox_ssh_key_public": str(ssh_public_key),
            }
            inventory["all"]["children"]["lxcs"]["vars"][
                "proxmox_fleet_observation_override"
            ] = None
            env = live_fixture_environment(temp_root, yaml.safe_dump(inventory))
            for proxy_name in (
                "ALL_PROXY",
                "HTTPS_PROXY",
                "HTTP_PROXY",
                "all_proxy",
                "https_proxy",
                "http_proxy",
            ):
                env.pop(proxy_name, None)
            env["NO_PROXY"] = "127.0.0.1,localhost"
            env["no_proxy"] = "127.0.0.1,localhost"
            result = run_inspect(
                "plan",
                "--limit",
                "target_conflict,release_problem",
                env=env,
                timeout=60,
            )
            api_requests = list(api_server.requests)  # type: ignore[attr-defined]

    output = f"{result.stdout}\n{result.stderr}"
    required_fragments = (
        "Standalone lifecycle validation found",
        "Target identity conflict",
        "VMID 5199",
        "Guest release observation is required",
        "release_problem",
        "SSH key authentication to controlled.invalid is not configured",
    )
    if result.returncode == 0 or not all(
        fragment in output for fragment in required_fragments
    ):
        raise AssertionError(f"public plan did not report every controlled problem:\n{output}")
    mutation_tasks = (
        "Prompt for Proxmox root password",
        "Add SSH public key to authorized_keys",
        "Set correct permissions on authorized_keys",
    )
    for task_name in mutation_tasks:
        task_start = output.find(f": {task_name}] ")
        task_end = output.find("\nTASK [", task_start + 1)
        task_output = output[task_start : task_end if task_end >= 0 else None]
        if task_start < 0 or "skipping:" not in task_output:
            raise AssertionError(
                f"public plan did not skip L3 mutation task {task_name!r}:\n{output}"
            )
    if "Password for root@" in output:
        raise AssertionError("public plan entered the password-driven mutation path")
    if not api_requests:
        raise AssertionError("public plan did not exercise the controlled API boundary")
    for credential_value in (
        CONTROLLED_API_USER,
        CONTROLLED_API_TOKEN_ID,
        CONTROLLED_API_TOKEN_SECRET,
    ):
        if credential_value in output:
            raise AssertionError("public plan disclosed a controlled credential value")


def main() -> int:
    try:
        assert_explicit_operation_and_help()
        assert_vars_masks_vault_derived_values_without_live_execution()
        assert_vars_normalizes_inventory_failure_without_disclosure()
        assert_vars_content_rule_ignores_seven_character_vault_values()
        assert_vars_graph_preserves_inventory_tree_without_live_execution()
        assert_vars_graph_normalizes_inventory_failure_without_disclosure()
        assert_operations_route_through_shared_live_execution()
        assert_exclusive_contention_names_holder()
        assert_diagnostic_playbooks_are_consolidated()
        assert_connectivity_fails_after_reporting_unreachable_targets()
        assert_containers_includes_unreserved_node_container()
        assert_credentials_walks_permission_ladder_without_disclosure()
        assert_public_plan_reports_all_problems_without_disclosure_or_mutation()
    except (AssertionError, subprocess.TimeoutExpired) as error:
        print(error, file=sys.stderr)
        return 1
    print("ok: inspect command exposes read-only managed-host diagnostics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
