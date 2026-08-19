#!/usr/bin/env python3
"""Test that stack_sync_materialize.yml renders templates and copies static files correctly."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "tests" / "regression" / "fixtures" / "stack_sync_materialize_test.yml"
ANSIBLE_PLAYBOOK = "uv run --locked ansible-playbook".split()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="stack-sync-materialize-") as temp_root:
        runs = [
            [
                *ANSIBLE_PLAYBOOK,
                str(PLAYBOOK),
                "-e",
                f"temp_root={temp_root}/apply",
                "-e",
                f"repo_root={REPO_ROOT}",
            ],
            [
                *ANSIBLE_PLAYBOOK,
                str(PLAYBOOK),
                "--check",
                "-e",
                f"temp_root={temp_root}/check",
                "-e",
                f"repo_root={REPO_ROOT}",
            ],
        ]
        results = [
            subprocess.run(
                command,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                env=os.environ.copy(),
            )
            for command in runs
        ]

    for mode, result in zip(("apply", "check"), results, strict=True):
        if result.returncode != 0:
            print(f"{mode} playbook failed unexpectedly", file=sys.stderr)
            print(f"{result.stdout}\n{result.stderr}", file=sys.stderr)
            return 1

    print("ok: materialize preserves directory and exact stack-file metadata contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
