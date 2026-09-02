#!/usr/bin/env python3
"""Regression tests for config/lxc_github_keys role."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ansible_test_helper import ansible_playbook_command


REPO_ROOT = Path(__file__).resolve().parents[2]
SINGLE_USER_PLAYBOOK = REPO_ROOT / "tests" / "regression" / "fixtures" / "lxc_github_keys_single_user_test.yml"
MULTI_USER_PLAYBOOK = REPO_ROOT / "tests" / "regression" / "fixtures" / "lxc_github_keys_multi_user_dedup_test.yml"
EMPTY_USERS_PLAYBOOK = REPO_ROOT / "tests" / "regression" / "fixtures" / "lxc_github_keys_empty_users_test.yml"
EMPTY_RESPONSE_PLAYBOOK = REPO_ROOT / "tests" / "regression" / "fixtures" / "lxc_github_keys_empty_response_test.yml"
ANSIBLE_PLAYBOOK = ansible_playbook_command()


def run_playbook(playbook: Path, temp_root: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*ANSIBLE_PLAYBOOK, str(playbook), "-f", "1", "-e", f"temp_root={temp_root}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lxc-github-keys-single-") as temp_root:
        single = run_playbook(SINGLE_USER_PLAYBOOK, temp_root)

    if single.returncode != 0:
        print("single-user playbook failed unexpectedly", file=sys.stderr)
        print(f"{single.stdout}\n{single.stderr}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="lxc-github-keys-multi-") as temp_root:
        multi = run_playbook(MULTI_USER_PLAYBOOK, temp_root)

    if multi.returncode != 0:
        print("multi-user playbook failed unexpectedly", file=sys.stderr)
        print(f"{multi.stdout}\n{multi.stderr}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="lxc-github-keys-empty-") as temp_root:
        empty = run_playbook(EMPTY_USERS_PLAYBOOK, temp_root)

    empty_output = f"{empty.stdout}\n{empty.stderr}"
    if empty.returncode == 0:
        print("empty-users playbook succeeded unexpectedly", file=sys.stderr)
        print(f"{empty.stdout}\n{empty.stderr}", file=sys.stderr)
        return 1

    markers = ["lxc_github_keys_github_users", "non-empty"]
    missing = [marker for marker in markers if marker not in empty_output]
    if missing:
        print(f"empty-users playbook output missed expected fragments: {missing}", file=sys.stderr)
        print(f"{empty.stdout}\n{empty.stderr}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="lxc-github-keys-empty-response-") as temp_root:
        empty_response = run_playbook(EMPTY_RESPONSE_PLAYBOOK, temp_root)

    empty_response_output = f"{empty_response.stdout}\n{empty_response.stderr}"
    if empty_response.returncode == 0:
        print("empty-response playbook succeeded unexpectedly", file=sys.stderr)
        print(empty_response_output, file=sys.stderr)
        return 1

    markers = ["GitHub", "returned no SSH keys", "lxc_github_keys_github_users"]
    missing = [marker for marker in markers if marker not in empty_response_output]
    if missing:
        print(f"empty-response playbook output missed expected fragments: {missing}", file=sys.stderr)
        print(empty_response_output, file=sys.stderr)
        return 1

    print("ok: lxc_github_keys writes keys correctly and fails clearly on empty users/results")
    return 0


def test_lxc_github_keys() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
