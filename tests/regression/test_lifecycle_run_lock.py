#!/usr/bin/env python3
"""Regression for the machine-local lifecycle wrapper lock and marker guard."""

from __future__ import annotations

import fcntl
import json
import os
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
METADATA_LOCK_RELATIVE_PATH = Path(".ansible/homelab-iac-lifecycle.lock.metadata")
ANSIBLE_PLAYBOOK = "uv run --locked ansible-playbook".split()
LIVE_EXECUTION_LIBRARY = REPO_ROOT / "scripts/lib/live-execution.sh"


def make_fake_uv(bin_dir: Path) -> None:
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env python3
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

capture = Path(os.environ["LIFECYCLE_TEST_CAPTURE"])
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
if mode in ("cleanup_contention_success", "cleanup_contention_fail"):
    metadata_path = Path(os.environ["HOME"]) / ".ansible/homelab-iac-lifecycle.lock.metadata"
    metadata_file = metadata_path.open("a+", encoding="utf-8")
    fcntl.flock(metadata_file, fcntl.LOCK_EX)
    holder = subprocess.Popen(
        ["sleep", "1.4"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(metadata_file.fileno(),),
    )
    metadata_file.seek(0)
    metadata_file.truncate()
    metadata_file.write(f"pid={holder.pid} worktree=/controlled/cleanup-holder\\n")
    metadata_file.flush()
    metadata_file.close()
    if mode == "cleanup_contention_fail":
        raise SystemExit(42)
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


def process_has_open_path(pid: int, expected_path: Path) -> bool:
    try:
        return any(
            descriptor.resolve() == expected_path
            for descriptor in Path(f"/proc/{pid}/fd").iterdir()
        )
    except (FileNotFoundError, PermissionError):
        return False


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

        sensitive_placeholder = "PLACEHOLDER_SENSITIVE_VALUE_206"
        for arguments in (
            (f"unknown-{sensitive_placeholder}",),
            (f"--unknown={sensitive_placeholder}",),
        ):
            result = run_wrapper(env, *arguments)
            output = f"{result.stdout}\n{result.stderr}"
            if result.returncode != 2 or sensitive_placeholder in output:
                raise AssertionError(
                    "invalid input did not return 2 with non-disclosing diagnostics:\n"
                    f"returncode={result.returncode}\n{output}"
                )


def assert_wrapper_routes_and_propagates() -> None:
    full_defaults = (
        "-e",
        "proxmox_lifecycle_intent=full",
        "-e",
        "proxmox_skip_self=true",
        '--extra-vars={"stack_filter":null}',
    )
    provision_defaults = (
        "-e",
        "proxmox_lifecycle_intent=provision_only",
        "-e",
        "proxmox_skip_self=true",
        '--extra-vars={"stack_filter":null}',
    )
    configure_defaults = (
        "-e",
        "proxmox_lifecycle_intent=configure_only",
        "-e",
        "proxmox_skip_self=true",
        '--extra-vars={"stack_filter":null}',
    )
    cases = (
        ((), "site.yml", full_defaults),
        (("full",), "site.yml", full_defaults),
        (("--limit", "cap_docker"), "site.yml", ("--limit", "cap_docker", *full_defaults)),
        (("--limit", "collie,portal,!workstation"), "site.yml", ("--limit", "collie,portal,!workstation", *full_defaults)),
        (("--check",), "site.yml", ("--check", *full_defaults)),
        (
            ("--stack", "beets"),
            "site.yml",
            (
                "-e",
                "proxmox_lifecycle_intent=full",
                "-e",
                "proxmox_skip_self=true",
                "-e",
                "stack_filter=beets",
            ),
        ),
        (
            ("--include-controller",),
            "site.yml",
            (
                "--limit",
                "workstation",
                "-e",
                "proxmox_lifecycle_intent=full",
                "-e",
                "proxmox_skip_self=false",
                '--extra-vars={"stack_filter":null}',
            ),
        ),
        (
            ("--include-controller", "--limit", "portal"),
            "site.yml",
            (
                "--limit",
                "workstation",
                "-e",
                "proxmox_lifecycle_intent=full",
                "-e",
                "proxmox_skip_self=false",
                '--extra-vars={"stack_filter":null}',
            ),
        ),
        (("-v",), "site.yml", ("-v", *full_defaults)),
        (("-vv",), "site.yml", ("-vv", *full_defaults)),
        (("-vvv",), "site.yml", ("-vvv", *full_defaults)),
        (("--", "--diff", "-e", "harmless=true"), "site.yml", ("--diff", "-e", "harmless=true", *full_defaults)),
        (("--", "--extra-vars=harmless=true"), "site.yml", ("--extra-vars=harmless=true", *full_defaults)),
        (("--", "-eharmless=true"), "site.yml", ("-eharmless=true", *full_defaults)),
        (("--", "-e", '{"harmless": true}'), "site.yml", ("-e", '{"harmless": true}', *full_defaults)),
        (("--", "--extra-vars", "{harmless: true}"), "site.yml", ("--extra-vars", "{harmless: true}", *full_defaults)),
        (("--", "-e", "@vaulted-vars.yml"), "site.yml", ("-e", "@vaulted-vars.yml", *full_defaults)),
        (("--", "-e", "proxmox_lifecycle_intent=configure_only"), "site.yml", ("-e", "proxmox_lifecycle_intent=configure_only", *full_defaults)),
        (("--", '--extra-vars={"stack_filter":"beets","proxmox_skip_self":false}'), "site.yml", ('--extra-vars={"stack_filter":"beets","proxmox_skip_self":false}', *full_defaults)),
        (("provision", "--limit", "collie"), "playbooks/provision-lxcs.yml", ("--limit", "collie", *provision_defaults)),
        (("configure", "--limit", "collie"), "playbooks/configure-lxcs.yml", ("--limit", "collie", *configure_defaults)),
        (("configure", "--", "--diff", "-e", "harmless=true"), "playbooks/configure-lxcs.yml", ("--diff", "-e", "harmless=true", *configure_defaults)),
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

    with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-nondisclosure-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root)
        sensitive_placeholder = "PLACEHOLDER_SENSITIVE_VALUE_206"
        extra_var = f"harmless={sensitive_placeholder}"
        proc = run_wrapper(env, "--", "-e", extra_var)
        capture = json.loads((temp_root / "capture.json").read_text(encoding="utf-8"))
        output = f"{proc.stdout}\n{proc.stderr}"
        if (
            proc.returncode != 0
            or extra_var not in capture["argv"]
            or sensitive_placeholder in output
        ):
            raise AssertionError(
                "passthrough was not forwarded exactly and kept out of terminal prose:\n"
                f"capture={capture!r}\n{output}"
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
        ("provision", "--", "--tags", "configure"),
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


def assert_contention_ignores_a_completed_holder_during_turnover() -> None:
    with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-holder-turnover-") as temp_dir:
        temp_root = Path(temp_dir)
        base_env = wrapper_environment(temp_root)

        completed_env = base_env.copy()
        completed_capture = temp_root / "completed-capture.json"
        completed_env.update(
            {
                "LIFECYCLE_TEST_CAPTURE": str(completed_capture),
                "LIFECYCLE_TEST_MODE": "delay",
                "LIFECYCLE_TEST_DELAY_SECONDS": "0.3",
            }
        )
        completed = subprocess.Popen(
            [str(RUNNER), "--check"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=completed_env,
        )
        deadline = time.monotonic() + 10
        while not completed_capture.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not completed_capture.exists():
            completed.kill()
            raise AssertionError("first turnover holder never launched")

        current_env = base_env.copy()
        current_capture = temp_root / "current-capture.json"
        current_env.update(
            {
                "LIFECYCLE_TEST_CAPTURE": str(current_capture),
                "LIFECYCLE_TEST_MODE": "sleep",
            }
        )
        current = subprocess.Popen(
            [str(RUNNER), "--check"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=current_env,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 10
            while not current_capture.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not current_capture.exists():
                raise AssertionError("successor shared holder never launched")
            current_child_pid = json.loads(
                current_capture.read_text(encoding="utf-8")
            )["pid"]
            completed_stdout, completed_stderr = completed.communicate(timeout=10)
            if completed.returncode != 0:
                raise AssertionError(
                    "first turnover holder did not complete cleanly:\n"
                    f"{completed_stdout}\n{completed_stderr}"
                )
            if current.poll() is not None:
                raise AssertionError("successor holder exited before turnover contention")

            contender_env = base_env.copy()
            contender_capture = temp_root / "turnover-contender-capture.json"
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
                or f"pid={current_child_pid}" not in output
                or f"worktree={REPO_ROOT}" not in output
                or contender_capture.exists()
            ):
                raise AssertionError(
                    "turnover contention reported stale rather than current metadata:\n"
                    f"{output}"
                )
        finally:
            if completed.poll() is None:
                completed.kill()
                completed.communicate(timeout=10)
            if current.poll() is None:
                os.killpg(current.pid, signal.SIGINT)
            current.communicate(timeout=10)


def assert_metadata_transitions_serialize_compatible_callers() -> None:
    with tempfile.TemporaryDirectory(prefix="lifecycle-metadata-serialization-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root, mode="delay")
        env["LIFECYCLE_TEST_DELAY_SECONDS"] = "0.2"
        metadata_path = Path(env["HOME"]) / METADATA_LOCK_RELATIVE_PATH
        metadata_path.parent.mkdir(parents=True)
        with metadata_path.open("w", encoding="utf-8") as metadata_file:
            fcntl.flock(metadata_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            caller = subprocess.Popen(
                [str(RUNNER), "--check"],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            deadline = time.monotonic() + 2
            while (
                caller.poll() is None
                and not process_has_open_path(caller.pid, metadata_path)
                and time.monotonic() < deadline
            ):
                time.sleep(0.005)
            if caller.poll() is not None or not process_has_open_path(
                caller.pid, metadata_path
            ):
                raise AssertionError("compatible caller rejected metadata serialization")
            time.sleep(0.04)
            if (temp_root / "capture.json").exists():
                caller.kill()
                raise AssertionError(
                    "compatible caller bypassed the metadata transition boundary"
                )
            fcntl.flock(metadata_file, fcntl.LOCK_UN)
        stdout, stderr = caller.communicate(timeout=10)
        if caller.returncode != 0 or not (temp_root / "capture.json").exists():
            raise AssertionError(
                "compatible caller did not continue after metadata serialization:\n"
                f"{stdout}\n{stderr}"
            )


def assert_metadata_coordination_has_a_bounded_failure() -> None:
    with tempfile.TemporaryDirectory(prefix="lifecycle-metadata-timeout-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root)
        metadata_path = Path(env["HOME"]) / METADATA_LOCK_RELATIVE_PATH
        metadata_path.parent.mkdir(parents=True)
        with metadata_path.open("w", encoding="utf-8") as metadata_file:
            metadata_file.write(
                f"pid={os.getpid()} worktree=/controlled/metadata-holder\n"
            )
            metadata_file.flush()
            fcntl.flock(metadata_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            start = time.monotonic()
            caller = subprocess.Popen(
                [str(RUNNER)],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
            try:
                stdout, stderr = caller.communicate(timeout=1)
            except subprocess.TimeoutExpired as error:
                os.killpg(caller.pid, signal.SIGKILL)
                caller.communicate(timeout=10)
                raise AssertionError("metadata acquisition waited beyond its bound") from error
            elapsed = time.monotonic() - start
        output = f"{stdout}\n{stderr}"
        if (
            caller.returncode != 75
            or elapsed < 0.05
            or elapsed >= 0.5
            or f"pid={os.getpid()}" not in output
            or "worktree=/controlled/metadata-holder" not in output
            or (temp_root / "capture.json").exists()
        ):
            raise AssertionError(
                f"bounded metadata failure lacked active holder identity ({elapsed:.2f}s):\n"
                f"{output}"
            )


def assert_cleanup_metadata_contention_preserves_playbook_status() -> None:
    for mode, expected_status in (
        ("cleanup_contention_success", 0),
        ("cleanup_contention_fail", 1),
    ):
        with tempfile.TemporaryDirectory(prefix="lifecycle-cleanup-contention-") as temp_dir:
            temp_root = Path(temp_dir)
            env = wrapper_environment(temp_root, mode=mode)
            result = run_wrapper(env)
            output = f"{result.stdout}\n{result.stderr}"
            holder_records = list(
                (Path(env["HOME"]) / f"{LOCK_RELATIVE_PATH}.holders").glob("*.holder")
            )
            lock_path = Path(env["HOME"]) / LOCK_RELATIVE_PATH
            with lock_path.open("a", encoding="utf-8") as lock_file:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise AssertionError(
                        f"cleanup contention left the lifecycle lock held for {mode}"
                    ) from error
            if (
                result.returncode != expected_status
                or not (temp_root / "capture.json").exists()
                or holder_records
                or "coordinates" in output
            ):
                raise AssertionError(
                    f"cleanup contention changed the normalized playbook result for {mode}:\n"
                    f"returncode={result.returncode}\n{output}"
                )


def assert_holder_metadata_covers_lock_lifetime() -> None:
    with tempfile.TemporaryDirectory(prefix="lifecycle-holder-lifetime-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root, mode="delay")
        env["LIFECYCLE_TEST_DELAY_SECONDS"] = "0.6"
        lock_path = Path(env["HOME"]) / LOCK_RELATIVE_PATH
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
        holder_records = list(Path(f"{lock_path}.holders").glob("*.holder"))
        if not capture_path.exists() or len(holder_records) != 1:
            holder.kill()
            raise AssertionError("held live lock lacked invocation metadata")
        with lock_path.open("a", encoding="utf-8") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                holder.kill()
                raise AssertionError("shared holder metadata existed without its live lock")
        stdout, stderr = holder.communicate(timeout=10)
        if holder.returncode != 0:
            raise AssertionError(
                "holder cleanup changed the playbook result:\n"
                f"{stdout}\n{stderr}"
            )
        if list(Path(f"{lock_path}.holders").glob("*.holder")):
            raise AssertionError("holder metadata outlived its released lock")


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


def assert_null_stack_filter_preserves_all_stack_behavior() -> None:
    discover_tasks = yaml.safe_load(
        (
            REPO_ROOT
            / "playbooks/roles/config/lxc_stack_sync/tasks/discover.yml"
        ).read_text(encoding="utf-8")
    )
    filter_task_names = {
        "Fail when stack_filter is used but stacks source is absent",
        "Assert stack_filter names a known stack",
        "Scope desired stacks to stack_filter",
        "Suppress stale stack list when stack_filter is active",
        "Scope per-host find results to stack_filter",
    }
    actual = {
        task["name"]: task.get("when")
        for task in discover_tasks
        if task.get("name") in filter_task_names
    }
    expected = {
        name: "stack_filter is defined and stack_filter is not none"
        for name in filter_task_names
    }
    if actual != expected:
        raise AssertionError(
            "canonical null must bypass every stack-filter-specific role task:\n"
            f"expected={expected!r}\nactual={actual!r}"
        )


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
        assert_contention_ignores_a_completed_holder_during_turnover()
        assert_metadata_transitions_serialize_compatible_callers()
        assert_cleanup_metadata_contention_preserves_playbook_status()
        assert_metadata_coordination_has_a_bounded_failure()
        assert_holder_metadata_covers_lock_lifetime()
        assert_live_execution_responsibilities_are_sourced()
        assert_mismatched_prerequisite_layers_prevent_execution()
        assert_check_mode_opt_out_audit_is_unchanged()
        assert_null_stack_filter_preserves_all_stack_behavior()
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
