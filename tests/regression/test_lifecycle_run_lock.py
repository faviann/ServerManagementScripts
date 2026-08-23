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


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "run.sh"
LOCK_RELATIVE_PATH = Path(".ansible/homelab-iac-lifecycle.lock")
ANSIBLE_PLAYBOOK = "uv run --locked ansible-playbook".split()


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


def assert_wrapper_routes_and_propagates() -> None:
    cases = (
        ((), "site.yml", ()),
        (("--limit", "collie", "--check"), "site.yml", ("--limit", "collie", "--check")),
        (("provision", "--limit", "collie"), "playbooks/provision-lxcs.yml", ("--limit", "collie")),
        (("configure", "--limit", "collie"), "playbooks/configure-lxcs.yml", ("--limit", "collie")),
    )
    for arguments, playbook, passthrough in cases:
        with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-routing-") as temp_dir:
            temp_root = Path(temp_dir)
            env = wrapper_environment(temp_root)
            proc = run_wrapper(env, *arguments)
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

    with tempfile.TemporaryDirectory(prefix="lifecycle-wrapper-failure-") as temp_dir:
        temp_root = Path(temp_dir)
        env = wrapper_environment(temp_root, mode="fail")
        first = run_wrapper(env)
        env["LIFECYCLE_TEST_MODE"] = "success"
        second = run_wrapper(env)
        if first.returncode != 42 or second.returncode != 0:
            raise AssertionError(
                "wrapper did not propagate failure or release the lock afterward:\n"
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
        assert_wrapper_routes_and_propagates()
        assert_contention_fails_fast()
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
