#!/usr/bin/env python3
"""Regression test for the stack sync role missing-source guardrail boundary."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ansible_test_helper import ansible_playbook_command


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "tests" / "regression" / "fixtures" / "stack_sync_reconciliation_missing_source_boundary.yml"
ANSIBLE_PLAYBOOK = ansible_playbook_command()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="stack-sync-missing-boundary-") as temp_root:
        proc = subprocess.run(
            [*ANSIBLE_PLAYBOOK, str(PLAYBOOK), "-e", f"temp_root={temp_root}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )

    output = f"{proc.stdout}\n{proc.stderr}"

    if proc.returncode == 0:
        print("expected stack sync role boundary to fail when source stacks are missing", file=sys.stderr)
        print(output, file=sys.stderr)
        return 1

    expected_bits = [
        "Missing per-host stack source directory",
        "unmanaged deployed stack directories still exist",
        "rogue",
    ]
    missing = [bit for bit in expected_bits if bit not in output]
    if missing:
        print("stack sync boundary failed, but not with the expected guardrail output", file=sys.stderr)
        print(f"missing fragments: {missing}", file=sys.stderr)
        print(output, file=sys.stderr)
        return 1

    print("ok: stack sync role boundary missing-source guardrail triggered as expected")
    return 0


def test_stack_sync_reconciliation_missing_source_boundary() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())