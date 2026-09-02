#!/usr/bin/env python3
"""Regression test for missing required stack_vars during stack sync render."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ansible_test_helper import ansible_playbook_command


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = [
    (
        "missing required stack var key",
        REPO_ROOT / "tests" / "regression" / "fixtures" / "stack_sync_missing_required_stack_vars_test.yml",
        ["stack_vars", "komga_password"],
    ),
    (
        "missing stack vars map",
        REPO_ROOT / "tests" / "regression" / "fixtures" / "stack_sync_missing_stack_vars_map_test.yml",
        ["stack_vars", "komga_user"],
    ),
]
ANSIBLE_PLAYBOOK = ansible_playbook_command()


def run_missing_stack_vars_case(name: str, playbook: Path, markers: list[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="stack-sync-missing-required-stack-vars-") as temp_root:
        env = os.environ.copy()
        env["ANSIBLE_LOCAL_TEMP"] = temp_root
        env["TMPDIR"] = temp_root
        proc = subprocess.run(
            [
                *ANSIBLE_PLAYBOOK,
                str(playbook),
                "-e",
                f"temp_root={temp_root}",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
        )

    output = f"{proc.stdout}\n{proc.stderr}"

    if proc.returncode == 0:
        print(f"expected playbook to fail for {name}", file=sys.stderr)
        print(output, file=sys.stderr)
        return 1

    missing = [marker for marker in markers if marker not in output]
    has_failure_kind = ("undefined" in output) or ("has no attribute" in output)
    if missing or not has_failure_kind:
        print(f"playbook failed for {name}, but not with the expected missing stack vars output", file=sys.stderr)
        if missing:
            print(f"missing fragments: {missing}", file=sys.stderr)
        if not has_failure_kind:
            print("missing failure kind marker: undefined or has no attribute", file=sys.stderr)
        print(output, file=sys.stderr)
        return 1

    return 0


def main() -> int:
    for name, playbook, markers in PLAYBOOKS:
        result = run_missing_stack_vars_case(name, playbook, markers)
        if result != 0:
            return result

    print("ok: missing required stack vars fail clearly")
    return 0


def test_stack_sync_missing_required_stack_vars() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
