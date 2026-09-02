#!/usr/bin/env python3
"""Test that stack_sync_start.yml creates external networks and brings up desired stacks only."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ansible_test_helper import ansible_playbook_command

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "tests" / "regression" / "fixtures" / "stack_sync_start_test.yml"
ANSIBLE_PLAYBOOK = ansible_playbook_command()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="stack-sync-start-") as temp_root:
        proc = subprocess.run(
            [*ANSIBLE_PLAYBOOK, str(PLAYBOOK), "-e", f"temp_root={temp_root}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )

    output = f"{proc.stdout}\n{proc.stderr}"

    if proc.returncode != 0:
        print("playbook failed unexpectedly", file=sys.stderr)
        print(output, file=sys.stderr)
        return 1

    print("ok: start creates networks and runs compose up only for desired stacks")
    return 0


def test_start_creates_networks() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
