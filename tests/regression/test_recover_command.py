#!/usr/bin/env python3
"""Regression coverage for the manual-recovery command."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RECOVER = REPO_ROOT / "recover.sh"


def run_recover(
    *arguments: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RECOVER), *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env or os.environ.copy(),
        timeout=15,
    )


def assert_explicit_operation_and_help() -> None:
    bare = run_recover()
    if bare.returncode != 2:
        raise AssertionError(f"bare recover returned {bare.returncode}, not 2")

    help_result = run_recover("--help")
    help_output = f"{help_result.stdout}\n{help_result.stderr}"
    if help_result.returncode != 0 or "ssh-keys" not in help_output:
        raise AssertionError(f"recover help is incomplete:\n{help_output}")


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

Path(os.environ["RECOVER_TEST_CAPTURE"]).write_text(json.dumps({
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
            "RECOVER_TEST_CAPTURE": str(temp_root / "capture.json"),
        }
    )
    return env


def assert_ssh_keys_routes_through_live_execution() -> None:
    cases = (
        ((), "lxcs"),
        (("--limit", "recovery-host"), "recovery-host"),
    )
    for options, prerequisite_target in cases:
        with tempfile.TemporaryDirectory(prefix="recover-command-") as temp_dir:
            temp_root = Path(temp_dir)
            env = controlled_environment(temp_root)
            result = run_recover("ssh-keys", *options, env=env)
            if result.returncode != 0:
                raise AssertionError(
                    f"ssh-keys failed unexpectedly:\n{result.stdout}\n{result.stderr}"
                )
            capture = json.loads(
                (temp_root / "capture.json").read_text(encoding="utf-8")
            )
            expected_arguments = [
                "run",
                "--locked",
                "ansible-playbook",
                "playbooks/add-ssh-keys-to-lxcs.yml",
                *options,
                "-e",
                f"prerequisite_target_pattern={prerequisite_target}",
            ]
            if capture != {"argv": expected_arguments, "marker": "1"}:
                raise AssertionError(
                    "ssh-keys did not use the live boundary with both prerequisite layers:\n"
                    f"expected={expected_arguments!r}\nactual={capture!r}"
                )


def assert_ssh_keys_fails_immediately_with_holder_identity() -> None:
    with tempfile.TemporaryDirectory(prefix="recover-command-lock-") as temp_dir:
        temp_root = Path(temp_dir)
        env = controlled_environment(temp_root)
        lock_path = Path(env["HOME"]) / ".ansible/homelab-iac-lifecycle.lock"
        holder_dir = Path(f"{lock_path}.holders")
        holder_dir.mkdir(parents=True)
        with lock_path.open("a", encoding="utf-8") as shared_holder:
            fcntl.flock(shared_holder, fcntl.LOCK_SH | fcntl.LOCK_NB)
            (holder_dir / f"{os.getpid()}.holder").write_text(
                f"pid={os.getpid()} parent_pid={os.getpid()} "
                "worktree=/controlled/recovery-holder\n",
                encoding="utf-8",
            )
            result = run_recover("ssh-keys", env=env)

        output = f"{result.stdout}\n{result.stderr}"
        if (
            result.returncode != 75
            or f"pid={os.getpid()}" not in output
            or "worktree=/controlled/recovery-holder" not in output
            or (temp_root / "capture.json").exists()
        ):
            raise AssertionError(
                "ssh-keys did not fail immediately with holder identity:\n"
                f"{output}"
            )


def main() -> int:
    assert_explicit_operation_and_help()
    assert_ssh_keys_routes_through_live_execution()
    assert_ssh_keys_fails_immediately_with_holder_identity()
    print("ok: recovery command requires an operation and describes ssh-keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
