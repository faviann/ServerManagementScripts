#!/usr/bin/env python3
"""Regression for the machine-local lifecycle wrapper lock and marker guard."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "run.sh"
LOCK_RELATIVE_PATH = Path(".ansible/homelab-iac-lifecycle.lock")
COORDINATOR_RELATIVE_PATH = Path(".ansible/homelab-iac-lifecycle.lock.metadata")
ANSIBLE_PLAYBOOK = "uv run --locked ansible-playbook".split()
LIVE_EXECUTION_LIBRARY = REPO_ROOT / "scripts/lib/live-execution.sh"


def make_fake_uv(bin_dir: Path) -> None:
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env python3
import json
import os
import signal
import sys
import time
from pathlib import Path

capture = Path(os.environ["LIFECYCLE_TEST_CAPTURE"])
if sys.argv[1:4] == ["run", "--locked", "python"]:
    real_uv = os.environ["LIFECYCLE_TEST_REAL_UV"]
    os.execv(real_uv, [real_uv, *sys.argv[1:]])
capture.write_text(json.dumps({
    "argv": sys.argv[1:],
    "marker": os.environ.get("HOMELAB_IAC_LIFECYCLE_WRAPPER"),
    "pid": os.getpid(),
}), encoding="utf-8")

def raise_signal_exit():
    raise SystemExit(130)

mode = os.environ.get("LIFECYCLE_TEST_MODE", "success")
if mode == "sleep":
    signal.signal(signal.SIGINT, lambda *_: raise_signal_exit())
    while True:
        time.sleep(1)
if mode == "delay":
    time.sleep(float(os.environ["LIFECYCLE_TEST_DELAY_SECONDS"]))
if mode == "fail":
    raise SystemExit(42)
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)


def wrapper_environment(temp_root: Path, *, mode: str = "success") -> dict[str, str]:
    home = temp_root / "home"
    bin_dir = temp_root / "bin"
    home.mkdir()
    bin_dir.mkdir()
    make_fake_uv(bin_dir)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "LIFECYCLE_TEST_CAPTURE": str(temp_root / "capture.json"),
            "LIFECYCLE_TEST_MODE": mode,
            "LIFECYCLE_TEST_REAL_UV": shutil.which("uv", path=env["PATH"]) or "uv",
        }
    )
    return env


def run_wrapper(env: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RUNNER), *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def assert_command_grammar_reports_help_and_usage_errors() -> None:
    with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-grammar-") as temp_dir:
        env = wrapper_environment(Path(temp_dir))
        help_result = run_wrapper(env, "--help")
        help_output = f"{help_result.stdout}\n{help_result.stderr}"
        if (
            help_result.returncode != 0
            or "full" not in help_output
            or "provision" not in help_output
            or "configure" not in help_output
        ):
            raise AssertionError(f"help did not describe lifecycle operations:\n{help_output}")

        for arguments in (("unknown-operation",), ("--unknown-option",)):
            result = run_wrapper(env, *arguments)
            if result.returncode != 2:
                raise AssertionError(
                    f"invalid input {arguments!r} returned {result.returncode}, expected 2:\n"
                    f"{result.stdout}\n{result.stderr}"
                )


def assert_wrapper_routes_and_propagates() -> None:
    cases = (
        ((), "site.yml", ()),
        (("full",), "site.yml", ()),
        (("--limit", "cap_docker"), "site.yml", ("--limit", "cap_docker")),
        (("--limit", "collie,portal,!workstation"), "site.yml", ("--limit", "collie,portal,!workstation")),
        (("--check",), "site.yml", ("--check",)),
        (("--stack", "beets"), "site.yml", ("-e", "stack_filter=beets")),
        (
            ("--include-controller",),
            "site.yml",
            ("--limit", "workstation", "-e", "proxmox_skip_self=false"),
        ),
        (
            ("--include-controller", "--limit", "portal"),
            "site.yml",
            ("--limit", "workstation", "-e", "proxmox_skip_self=false"),
        ),
        (("-v",), "site.yml", ("-v",)),
        (("-vv",), "site.yml", ("-vv",)),
        (("-vvv",), "site.yml", ("-vvv",)),
        (("--", "--diff", "-e", "harmless=true"), "site.yml", ("--diff", "-e", "harmless=true")),
        (("--", "--extra-vars=harmless=true"), "site.yml", ("--extra-vars=harmless=true",)),
        (("--", "-eharmless=true"), "site.yml", ("-eharmless=true",)),
        (("--", "-e", '{"harmless": true}'), "site.yml", ("-e", '{"harmless": true}')),
        (("--", "--extra-vars", "{harmless: true}"), "site.yml", ("--extra-vars", "{harmless: true}")),
        (("provision", "--limit", "collie"), "playbooks/provision-lxcs.yml", ("--limit", "collie")),
        (("configure", "--limit", "collie"), "playbooks/configure-lxcs.yml", ("--limit", "collie")),
    )
    for arguments, playbook, passthrough in cases:
        with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-routing-") as temp_dir:
            temp_root = Path(temp_dir)
            env = wrapper_environment(temp_root)
            proc = run_wrapper(env, *arguments)
            if not (temp_root / "capture.json").exists():
                raise AssertionError(
                    f"wrapper did not launch for {arguments!r}: returncode={proc.returncode}\n"
                    f"{proc.stdout}\n{proc.stderr}"
                )
            capture = json.loads((temp_root / "capture.json").read_text(encoding="utf-8"))
            expected = ["run", "--locked", "ansible-playbook", playbook, *passthrough]
            if (
                proc.returncode != 0
                or capture["argv"] != expected
                or capture["marker"] != "1"
                or not isinstance(capture["pid"], int)
            ):
                raise AssertionError(
                    f"wrapper routing mismatch for {arguments!r}: capture={capture!r}\n"
                    f"{proc.stdout}\n{proc.stderr}"
                )

    protected_passthrough = (
        ("--", "--limit", "portal"),
        ("--", "--check"),
        ("--", "--tags", "provision"),
        ("--", "-t", "configure"),
        ("--", "--tag=configure"),
        ("--", "--sk", "validation"),
        ("--", "--skip-t", "validation"),
        ("--", "--inventory", "inventory/other.yml"),
        ("--", "--inventory-file=inventory/other.yml"),
        ("--", "--inventory-f", "inventory/other.yml"),
        ("--", "-i", "inventory/other.yml"),
        ("--", "--lim=portal"),
        ("--", "--ch"),
        ("--", "--start-at-task", "Apply lifecycle actions"),
        ("--", "--sta", "Apply lifecycle actions"),
        ("--", "--start-a=Apply lifecycle actions"),
        ("--", "-e", "proxmox_lifecycle_intent=configure_only"),
        ("--", "--extra-vars={\"proxmox_lifecycle_intent\":\"configure_only\"}"),
        ("--", "-e", "{\"proxmox_\\u006cifecycle_intent\":\"configure_only\"}"),
        ("--", "-e", "stack_filter=beets"),
        ("--", "-e", "proxmox_skip_self=false"),
        ("provision", "--", "--tags", "configure"),
        ("configure", "--", "-e", "proxmox_lifecycle_intent=full"),
        ("--check", "--", "--limit", "portal"),
    )
    for arguments in protected_passthrough:
        with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-protection-") as temp_dir:
            temp_root = Path(temp_dir)
            env = wrapper_environment(temp_root)
            proc = run_wrapper(env, *arguments)
            if proc.returncode != 2 or (temp_root / "capture.json").exists():
                raise AssertionError(
                    f"protected passthrough {arguments!r} was not rejected:\n"
                    f"{proc.stdout}\n{proc.stderr}"
                )

    with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-extra-vars-file-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root)
        harmless_vars = temp_root / "harmless-vars.yml"
        harmless_vars.write_text("harmless: true\n", encoding="utf-8")
        argument = f"@{harmless_vars}"
        proc = run_wrapper(env, "--", "-e", argument)
        capture = json.loads((temp_root / "capture.json").read_text(encoding="utf-8"))
        if proc.returncode != 0 or capture["argv"][-2:] != ["-e", argument]:
            raise AssertionError(
                "harmless file-backed extra vars did not launch unchanged:\n"
                f"{proc.stdout}\n{proc.stderr}"
            )

    with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-protected-vars-file-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root)
        protected_vars = temp_root / "protected-vars.yml"
        protected_vars.write_text("proxmox_lifecycle_intent: configure_only\n", encoding="utf-8")
        proc = run_wrapper(env, "--", "-e", f"@{protected_vars}")
        if proc.returncode != 2 or (temp_root / "capture.json").exists():
            raise AssertionError(
                "protected file-backed extra vars launched the playbook:\n"
                f"{proc.stdout}\n{proc.stderr}"
            )

    with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-relative-vars-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root)
        caller_directory = temp_root / "caller"
        caller_directory.mkdir()
        colliding_vars = caller_directory / RUNNER.name
        colliding_vars.write_text("harmless: true\n", encoding="utf-8")
        proc = subprocess.run(
            [str(RUNNER), "--", "-e", f"@{RUNNER.name}"],
            cwd=caller_directory,
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        capture = json.loads((temp_root / "capture.json").read_text(encoding="utf-8"))
        expected_argument = f"@{colliding_vars.resolve()}"
        if proc.returncode != 0 or capture["argv"][-2:] != ["-e", expected_argument]:
            raise AssertionError(
                "relative extra-vars file inspection and execution identities diverged:\n"
                f"capture={capture!r}\n{proc.stdout}\n{proc.stderr}"
            )

    for arguments in (("--limit",), ("--stack",), ("--limit", "--check")):
        with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-missing-value-") as temp_dir:
            temp_root = Path(temp_dir)
            env = wrapper_environment(temp_root)
            proc = run_wrapper(env, *arguments)
            if proc.returncode != 2 or (temp_root / "capture.json").exists():
                raise AssertionError(
                    f"missing option value {arguments!r} was not rejected:\n"
                    f"{proc.stdout}\n{proc.stderr}"
                )

    with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-failure-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root, mode="fail")
        first = run_wrapper(env)
        env["LIFECYCLE_TEST_MODE"] = "success"
        second = run_wrapper(env)
        if first.returncode != 1 or second.returncode != 0:
            raise AssertionError(
                "wrapper did not normalize failure or release the lock afterward:\n"
                f"first={first.returncode}\n{first.stdout}\n{first.stderr}\n"
                f"second={second.returncode}\n{second.stdout}\n{second.stderr}"
            )


def assert_contention_fails_fast() -> None:
    with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-contention-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root)
        lock_path = Path(env["HOME"]) / LOCK_RELATIVE_PATH
        lock_path.parent.mkdir(parents=True)
        with lock_path.open("w", encoding="utf-8") as lock_file:
            lock_file.write("pid=4242 worktree=/controlled/holder\n")
            lock_file.flush()
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            start = time.monotonic()
            proc = run_wrapper(env)
            elapsed = time.monotonic() - start
        output = f"{proc.stdout}\n{proc.stderr}"
        if (
            proc.returncode == 0
            or elapsed >= 2
            or "pid=4242" not in output
            or "/controlled/holder" not in output
            or (temp_root / "capture.json").exists()
        ):
            raise AssertionError(
                f"contending run did not fail fast with holder identity ({elapsed:.2f}s):\n{output}"
            )


def assert_lock_class_follows_operation_class() -> None:
    cases = (
        (fcntl.LOCK_SH, (), 75),
        (fcntl.LOCK_SH, ("provision",), 75),
        (fcntl.LOCK_SH, ("configure",), 75),
        (fcntl.LOCK_SH, ("--check",), 0),
        (fcntl.LOCK_EX, ("--check",), 75),
    )
    for held_class, arguments, expected_returncode in cases:
        with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-lock-class-") as temp_dir:
            temp_root = Path(temp_dir)
            env = wrapper_environment(temp_root)
            lock_path = Path(env["HOME"]) / LOCK_RELATIVE_PATH
            lock_path.parent.mkdir(parents=True)
            with lock_path.open("w", encoding="utf-8") as lock_file:
                lock_file.write("pid=4242 worktree=/controlled/holder\n")
                lock_file.flush()
                fcntl.flock(lock_file, held_class | fcntl.LOCK_NB)
                start = time.monotonic()
                proc = run_wrapper(env, *arguments)
                elapsed = time.monotonic() - start
            if proc.returncode != expected_returncode:
                raise AssertionError(
                    f"wrong lock behavior for {arguments!r} with held class {held_class}: "
                    f"returned {proc.returncode}, expected {expected_returncode}\n"
                    f"{proc.stdout}\n{proc.stderr}"
                )
            if expected_returncode == 75:
                output = f"{proc.stdout}\n{proc.stderr}"
                if elapsed >= 2 or "pid=4242" not in output or "/controlled/holder" not in output:
                    raise AssertionError(
                        f"contention for {arguments!r} did not fail fast with holder identity "
                        f"({elapsed:.2f}s):\n{output}"
                    )


def assert_contention_names_a_remaining_shared_holder() -> None:
    with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-shared-holders-") as temp_dir:
        temp_root = Path(temp_dir)
        base_env = wrapper_environment(temp_root)

        first_env = base_env.copy()
        first_capture = temp_root / "first-capture.json"
        first_env.update(
            {
                "LIFECYCLE_TEST_CAPTURE": str(first_capture),
                "LIFECYCLE_TEST_MODE": "sleep",
            }
        )
        first = subprocess.Popen(
            [str(RUNNER), "--check"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=first_env,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 10
            while not first_capture.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not first_capture.exists():
                raise AssertionError("first shared holder never launched")
            first_child_pid = json.loads(first_capture.read_text(encoding="utf-8"))["pid"]

            second_env = base_env.copy()
            second_capture = temp_root / "second-capture.json"
            second_env.update(
                {
                    "LIFECYCLE_TEST_CAPTURE": str(second_capture),
                    "LIFECYCLE_TEST_MODE": "delay",
                    "LIFECYCLE_TEST_DELAY_SECONDS": "0.3",
                }
            )
            second = subprocess.Popen(
                [str(RUNNER), "--check"],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=second_env,
            )
            second_stdout, second_stderr = second.communicate(timeout=10)
            if second.returncode != 0 or not second_capture.exists():
                raise AssertionError(
                    "later shared holder did not overlap and exit cleanly:\n"
                    f"{second_stdout}\n{second_stderr}"
                )
            if first.poll() is not None:
                raise AssertionError("earlier shared holder exited before contention")

            contender_env = base_env.copy()
            contender_capture = temp_root / "contender-capture.json"
            contender_env.update(
                {
                    "LIFECYCLE_TEST_CAPTURE": str(contender_capture),
                    "LIFECYCLE_TEST_MODE": "success",
                }
            )
            contender = run_wrapper(contender_env)
            output = f"{contender.stdout}\n{contender.stderr}"
            if (
                contender.returncode != 75
                or f"pid={first_child_pid}" not in output
                or f"worktree={REPO_ROOT}" not in output
                or contender_capture.exists()
            ):
                raise AssertionError(
                    "contention did not identify the remaining shared holder:\n"
                    f"{output}"
                )
        finally:
            if first.poll() is None:
                os.killpg(first.pid, signal.SIGINT)
            first.communicate(timeout=10)


def assert_holder_transitions_are_serialized() -> None:
    with tempfile.TemporaryDirectory(prefix="lifecycle-holder-publication-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root, mode="delay")
        env["LIFECYCLE_TEST_DELAY_SECONDS"] = "0.2"
        lock_path = Path(env["HOME"]) / LOCK_RELATIVE_PATH
        coordinator_path = Path(env["HOME"]) / COORDINATOR_RELATIVE_PATH
        coordinator_path.parent.mkdir(parents=True)
        with coordinator_path.open("w", encoding="utf-8") as coordinator:
            fcntl.flock(coordinator, fcntl.LOCK_EX | fcntl.LOCK_NB)
            holder = subprocess.Popen(
                [str(RUNNER), "--check"],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            try:
                deadline = time.monotonic() + 0.75
                while holder.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                if holder.poll() is not None:
                    raise AssertionError(
                        "holder did not enter the metadata-coordinated acquisition boundary"
                    )
                with lock_path.open("a", encoding="utf-8") as lock_file:
                    try:
                        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as error:
                        raise AssertionError(
                            "holder acquired the live lock before publishing metadata"
                        ) from error
            finally:
                fcntl.flock(coordinator, fcntl.LOCK_UN)
            stdout, stderr = holder.communicate(timeout=10)
            if holder.returncode != 0:
                raise AssertionError(
                    f"coordinated holder did not complete:\n{stdout}\n{stderr}"
                )

    with tempfile.TemporaryDirectory(prefix="lifecycle-holder-removal-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root, mode="delay")
        env["LIFECYCLE_TEST_DELAY_SECONDS"] = "0.2"
        lock_path = Path(env["HOME"]) / LOCK_RELATIVE_PATH
        coordinator_path = Path(env["HOME"]) / COORDINATOR_RELATIVE_PATH
        holder = subprocess.Popen(
            [str(RUNNER), "--check"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        capture_path = temp_root / "capture.json"
        deadline = time.monotonic() + 10
        while not capture_path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not capture_path.exists():
            holder.kill()
            raise AssertionError("shared holder never launched for cleanup ordering")
        child_pid = json.loads(capture_path.read_text(encoding="utf-8"))["pid"]

        with coordinator_path.open("a", encoding="utf-8") as coordinator:
            fcntl.flock(coordinator, fcntl.LOCK_EX)
            deadline = time.monotonic() + 10
            while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            if holder.poll() is not None:
                raise AssertionError("holder removed metadata before entering coordinated cleanup")
            with lock_path.open("a", encoding="utf-8") as lock_file:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise AssertionError("holder unlocked before coordinated metadata cleanup")
            holder_records = list(Path(f"{lock_path}.holders").glob("*.holder"))
            if len(holder_records) != 1 or f"parent_pid={holder.pid}" not in holder_records[0].read_text(
                encoding="utf-8"
            ):
                raise AssertionError("coordinated cleanup lacks an active parent holder record")
            fcntl.flock(coordinator, fcntl.LOCK_UN)

        stdout, stderr = holder.communicate(timeout=10)
        if holder.returncode != 0:
            raise AssertionError(f"holder cleanup failed:\n{stdout}\n{stderr}")
        if list(Path(f"{lock_path}.holders").glob("*.holder")):
            raise AssertionError("holder cleanup left invocation metadata behind")


def assert_live_execution_responsibilities_are_sourced() -> None:
    runner_source = RUNNER.read_text(encoding="utf-8")
    if runner_source.count("source \"$PROJECT_ROOT/scripts/lib/live-execution.sh\"") != 1:
        raise AssertionError("run.sh must source exactly one live-execution library")
    forbidden_runner_fragments = (
        "flock ",
        "HOMELAB_IAC_LIFECYCLE_WRAPPER",
        "uv run --locked ansible-playbook",
        "homelab-iac-lifecycle.lock",
    )
    present = [fragment for fragment in forbidden_runner_fragments if fragment in runner_source]
    if present:
        raise AssertionError(f"run.sh retains live-execution responsibilities: {present}")
    if 'run_live_playbook "$lock_class" control-node,proxmox-host' not in runner_source:
        raise AssertionError("run.sh must select the L1 and L3 prerequisite layers")

    library_source = LIVE_EXECUTION_LIBRARY.read_text(encoding="utf-8")
    required_library_fragments = (
        "flock --shared --nonblock",
        "flock --exclusive --nonblock",
        "HOMELAB_IAC_LIFECYCLE_WRAPPER",
        "control-node,proxmox-host",
        "uv run --locked ansible-playbook",
    )
    missing = [fragment for fragment in required_library_fragments if fragment not in library_source]
    if missing:
        raise AssertionError(f"live-execution library is missing responsibilities: {missing}")


def assert_mismatched_prerequisite_layers_prevent_execution() -> None:
    with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-layer-mismatch-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root)
        mismatched_runner = temp_root / "run.sh"
        mismatched_runner.write_text(
            RUNNER.read_text(encoding="utf-8").replace(
                'run_live_playbook "$lock_class" control-node,proxmox-host',
                'run_live_playbook "$lock_class" control-node',
            ),
            encoding="utf-8",
        )
        mismatched_runner.chmod(0o755)
        library_path = temp_root / "scripts/lib/live-execution.sh"
        library_path.parent.mkdir(parents=True)
        library_path.write_text(
            LIVE_EXECUTION_LIBRARY.read_text(encoding="utf-8"), encoding="utf-8"
        )

        proc = subprocess.run(
            [str(mismatched_runner)],
            cwd=temp_root,
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        if proc.returncode != 2 or (temp_root / "capture.json").exists():
            raise AssertionError(
                "a lifecycle command with incomplete prerequisite layers launched:\n"
                f"{proc.stdout}\n{proc.stderr}"
            )


def assert_check_mode_opt_out_audit_is_unchanged() -> None:
    expected = {
        ("playbooks/roles/config/lxc_nvidia_runtime/tasks/main.yml", "Verify NVIDIA runtime is registered with Docker"),
        ("playbooks/roles/config/lxc_docker_runtime/tasks/main.yml", "Verify Docker installation"),
        ("playbooks/roles/config/lxc_docker_runtime/tasks/main.yml", "Verify Docker Compose installation"),
        ("playbooks/roles/config/lxc_workstation_baseline/tasks/origin_firewall.yml", "Resolve workstation origin firewall allowlist address"),
        ("playbooks/roles/config/lxc_workstation_baseline/tasks/persistent_home.yml", "Inspect existing mount status for persistent home paths"),
        ("playbooks/roles/infrastructure/proxmox_host_bootstrap/tasks/ssh_access.yml", "Test if SSH key authentication already works"),
        ("playbooks/roles/infrastructure/proxmox_host_bootstrap/tasks/validation.yml", "Check if pct command is available"),
        ("playbooks/roles/infrastructure/proxmox_host_bootstrap/tasks/validation.yml", "Verify pct command works"),
        ("playbooks/roles/infrastructure/proxmox_host_bootstrap/tasks/validation.yml", "Check installed lxc-pve version"),
        ("playbooks/roles/infrastructure/proxmox_host_bootstrap/tasks/validation.yml", "Assert lxc-pve meets nested Docker minimum"),
        ("playbooks/roles/infrastructure/proxmox_lxc_host_config/tasks/config_file_bind_mounts.yml", "Get current bind mounts"),
        ("playbooks/roles/infrastructure/proxmox_lxc_host_config/tasks/config_file_wireguard.yml", "Get current WireGuard tun device access"),
        ("playbooks/roles/infrastructure/proxmox_lxc_host_config/tasks/config_file_nvidia.yml", "Get current NVIDIA GPU configuration lines"),
        ("playbooks/roles/infrastructure/proxmox_lxc_host_config/tasks/config_file_sysctls.yml", "Get current sysctl and AppArmor configuration"),
        ("playbooks/roles/infrastructure/proxmox_lxc_host_config/tasks/config_file_idmap.yml", "Get current UID/GID ID mappings"),
    }

    actual: set[tuple[str, str]] = set()

    def collect(value: object, relative_path: str) -> None:
        if isinstance(value, dict):
            if value.get("check_mode") is False and isinstance(value.get("name"), str):
                actual.add((relative_path, value["name"]))
            for child in value.values():
                collect(child, relative_path)
        elif isinstance(value, list):
            for child in value:
                collect(child, relative_path)

    for task_file in sorted((REPO_ROOT / "playbooks/roles").rglob("*.yml")):
        collect(
            yaml.safe_load(task_file.read_text(encoding="utf-8")),
            task_file.relative_to(REPO_ROOT).as_posix(),
        )
    if actual != expected:
        raise AssertionError(
            "production check_mode: false task snapshot changed:\n"
            f"missing={sorted(expected - actual)!r}\nadded={sorted(actual - expected)!r}"
        )

    module_source = (REPO_ROOT / "library/proxmox_pct.py").read_text(encoding="utf-8")
    if "supports_check_mode=True" not in module_source:
        raise AssertionError("library/proxmox_pct.py must declare check-mode support")


def assert_lock_decision_amends_linear_execution_adr() -> None:
    original = (REPO_ROOT / "docs/adr/0001-preserve-linear-lxc-execution.md").read_text(
        encoding="utf-8"
    )
    amendment_path = REPO_ROOT / "docs/adr/0009-split-live-execution-locks-by-operation-class.md"
    if not amendment_path.exists():
        raise AssertionError("ADR 0009 must record the live lock split")
    amendment = amendment_path.read_text(encoding="utf-8")
    required_amendment_terms = (
        "shared",
        "exclusive",
        "machine-local",
        "fail immediately",
        "ADR-0001",
        "amends",
    )
    missing = [term for term in required_amendment_terms if term not in amendment]
    if missing or "ADR-0009" not in original or "amended" not in original:
        raise AssertionError(
            f"lock ADR amendment relationship is incomplete: missing={missing!r}"
        )


def assert_interrupt_releases_lock() -> None:
    with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-interrupt-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root, mode="sleep")
        proc = subprocess.Popen(
            [str(RUNNER)],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
        capture_path = temp_root / "capture.json"
        deadline = time.monotonic() + 10
        while not capture_path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not capture_path.exists():
            proc.kill()
            raise AssertionError("wrapper never launched the controlled playbook process")
        os.killpg(proc.pid, signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=10)

        lock_path = Path(env["HOME"]) / LOCK_RELATIVE_PATH
        with lock_path.open("a", encoding="utf-8") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise AssertionError(
                    f"lock remained held after interrupt:\n{stdout}\n{stderr}"
                ) from error
        env["LIFECYCLE_TEST_MODE"] = "success"
        subsequent = run_wrapper(env)
        if subsequent.returncode != 0:
            raise AssertionError(
                "subsequent lifecycle run failed after the interrupted holder exited:\n"
                f"{subsequent.stdout}\n{subsequent.stderr}"
            )


def assert_wrapper_crash_keeps_child_lock() -> None:
    with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-crash-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root, mode="sleep")
        proc = subprocess.Popen(
            [str(RUNNER)],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        capture_path = temp_root / "capture.json"
        deadline = time.monotonic() + 10
        while not capture_path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not capture_path.exists():
            proc.kill()
            raise AssertionError("wrapper never launched the controlled playbook process")
        child_pid = json.loads(capture_path.read_text(encoding="utf-8"))["pid"]
        proc.kill()
        proc.wait(timeout=10)

        lock_path = Path(env["HOME"]) / LOCK_RELATIVE_PATH
        with lock_path.open("a", encoding="utf-8") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                os.kill(child_pid, signal.SIGKILL)
                raise AssertionError(
                    "lock was released while the lifecycle child was still running"
                )

        os.kill(child_pid, signal.SIGKILL)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with lock_path.open("a", encoding="utf-8") as lock_file:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    time.sleep(0.05)
                else:
                    break
        else:
            raise AssertionError("lock remained held after the lifecycle child exited")


def assert_direct_lifecycle_run_is_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="lifecycle-marker-guard-") as temp_dir:
        temp_root = Path(temp_dir)
        inventory = temp_root / "inventory.ini"
        vault = temp_root / "vault-pass"
        inventory.write_text("[local]\nlocalhost ansible_connection=local\n", encoding="utf-8")
        vault.write_text("unused-fixture-placeholder\n", encoding="utf-8")
        env = os.environ.copy()
        env.pop("HOMELAB_IAC_LIFECYCLE_WRAPPER", None)
        env["ANSIBLE_INVENTORY"] = str(inventory)
        env["ANSIBLE_VAULT_PASSWORD_FILE"] = str(vault)
        proc = subprocess.run(
            [*ANSIBLE_PLAYBOOK, "site.yml"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        output = f"{proc.stdout}\n{proc.stderr}"
        if (
            proc.returncode == 0
            or "./run.sh" not in output
            or "Check for uv-managed control node virtual environment" in output
            or "TASK [infrastructure/proxmox_host_bootstrap" in output
        ):
            raise AssertionError(f"direct lifecycle run was not rejected first:\n{output}")


def main() -> int:
    try:
        assert_command_grammar_reports_help_and_usage_errors()
        assert_wrapper_routes_and_propagates()
        assert_contention_fails_fast()
        assert_lock_class_follows_operation_class()
        assert_contention_names_a_remaining_shared_holder()
        assert_holder_transitions_are_serialized()
        assert_live_execution_responsibilities_are_sourced()
        assert_mismatched_prerequisite_layers_prevent_execution()
        assert_check_mode_opt_out_audit_is_unchanged()
        assert_lock_decision_amends_linear_execution_adr()
        assert_interrupt_releases_lock()
        assert_wrapper_crash_keeps_child_lock()
        assert_direct_lifecycle_run_is_rejected()
    except (AssertionError, subprocess.TimeoutExpired) as error:
        print(error, file=sys.stderr)
        return 1
    print("ok: lifecycle wrapper serializes machine-local lifecycle mutation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
