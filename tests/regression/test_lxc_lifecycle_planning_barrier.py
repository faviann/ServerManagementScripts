#!/usr/bin/env python3
"""Thin runner for the targeted LXC lifecycle planning barrier fixture."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ansible_test_helper import ansible_playbook_command


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "regression" / "fixtures"
ASSETS = FIXTURES / "lxc_lifecycle_facade_assets"
INVENTORY = FIXTURES / "lxc_lifecycle_planning_barrier_inventory.yml"
PLAYBOOK = FIXTURES / "lxc_lifecycle_planning_barrier_test.yml"
ANSIBLE_PLAYBOOK = ansible_playbook_command(supplies_own_inventory=True)


def run_case(temp_root: Path, name: str, *arguments: str) -> bool:
    state_dir = temp_root / name
    state_dir.mkdir()
    for vmid, state in (
        (7101, "stopped"),
        (7102, "absent"),
        (7103, "stopped"),
        (7104, "absent"),
        (7105, "stopped"),
        (7106, "running"),
    ):
        (state_dir / f"{vmid}.state").write_text(state, encoding="utf-8")
        (state_dir / f"{vmid}.release").write_text("12", encoding="utf-8")
        (state_dir / f"{vmid}.events").write_text("", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{ASSETS / 'bin'}:{env['PATH']}"
    env["LIFECYCLE_TEST_STATE_DIR"] = str(state_dir)
    # ansible.cfg names a vault password file that must exist, but the
    # fixture decrypts nothing: a placeholder keeps the run credential-free.
    vault_placeholder = state_dir / "vault-pass"
    vault_placeholder.write_text("unused-fixture-placeholder\n", encoding="utf-8")
    env["ANSIBLE_VAULT_PASSWORD_FILE"] = str(vault_placeholder)
    env["ANSIBLE_ROLES_PATH"] = os.pathsep.join(
        [str(ASSETS / "roles"), str(REPO_ROOT / "playbooks" / "roles")]
    )
    env["ANSIBLE_COLLECTIONS_PATH"] = os.pathsep.join(
        [str(ASSETS / "collections"), str(REPO_ROOT / "collections")]
    )
    proc = subprocess.Popen(
        [
            *ANSIBLE_PLAYBOOK,
            "-i",
            str(INVENTORY),
            str(PLAYBOOK),
            "-e",
            f"lifecycle_test_state_dir={state_dir}",
            "-e",
            f"lifecycle_test_case={name}",
            *arguments,
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    # Hang guard only: cases run concurrently, so allow contention headroom.
    # A check-mode regression that contacts the host still fails fast through
    # ansible_connect_timeout=1 and the case assertions, not this timeout.
    timeout_seconds = 120
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
        print(f"lifecycle planning barrier case {name!r} timed out", file=sys.stderr)
        print(error.stdout or "", file=sys.stderr)
        return False
    expected_failure = name in {"barrier", "execution_failure", "compile_failure"}
    assertions_complete = f"lifecycle_case_assertions_complete={name}" in stdout
    controlled_failure = (
        "Barrier fixture published every targeted result before returning" in stdout
    )
    succeeded = (
        (proc.returncode != 0 and controlled_failure and assertions_complete)
        if expected_failure
        else proc.returncode == 0 and assertions_complete
    )
    if name == "check":
        succeeded = succeeded and "Check mode skips guest configuration" in stdout
    if name == "check_running":
        succeeded = succeeded and "Model successful base-system configuration" in stdout
    if not succeeded:
        print(f"lifecycle planning barrier case {name!r} failed", file=sys.stderr)
        print(f"{stdout}\n{stderr}", file=sys.stderr)
        return False
    return True


CASES: tuple[tuple[str, ...], ...] = (
    ("barrier", "--limit", "barrier_valid,barrier_invalid"),
    ("limited", "--limit", "barrier_valid"),
    ("check", "--limit", "barrier_valid", "--check"),
    ("check_running", "--limit", "check_running", "--check"),
    (
        "execution_failure",
        "--limit",
        "barrier_valid,barrier_after_failure",
        "-e",
        "lifecycle_test_execution_failure_host=barrier_valid",
    ),
    ("compile_failure", "--limit", "barrier_valid,barrier_compile_invalid"),
    ("self_skip", "--limit", "workstation"),
    ("self_include", "--limit", "workstation", "-e", "proxmox_skip_self=false"),
    ("self_include_then_skip", "--limit", "workstation"),
    ("unsafe_default", "--limit", "barrier_valid,barrier_after_failure"),
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lxc-planning-barrier-") as temp_dir:
        temp_root = Path(temp_dir)
        # Every case owns an isolated state directory and ansible-playbook
        # process, so the case matrix runs concurrently for fast feedback.
        with ThreadPoolExecutor(max_workers=len(CASES)) as executor:
            cases = list(
                executor.map(
                    lambda case: run_case(temp_root, case[0], *case[1:]),
                    CASES,
                )
            )
        if not all(cases):
            return 1

    print("ok: lifecycle barrier covers limits, check mode, and fail-fast execution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
