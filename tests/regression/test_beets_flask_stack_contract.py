#!/usr/bin/env python3
"""Regression test for the beets-flask stack render contract."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ansible_test_helper import ansible_playbook_command


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "tests" / "regression" / "fixtures" / "stack_sync_beets_flask_materialize_test.yml"
ANSIBLE_PLAYBOOK = ansible_playbook_command()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="stack-sync-beets-flask-") as temp_root:
        env = os.environ.copy()
        env["ANSIBLE_LOCAL_TEMP"] = temp_root
        env["TMPDIR"] = temp_root
        proc = subprocess.run(
            [
                *ANSIBLE_PLAYBOOK,
                str(PLAYBOOK),
                "-e",
                f"temp_root={temp_root}",
                "-e",
                f"repo_root={REPO_ROOT}",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
        )

    output = f"{proc.stdout}\n{proc.stderr}"

    if proc.returncode != 0:
        print("beets-flask materialize playbook failed unexpectedly", file=sys.stderr)
        print(output, file=sys.stderr)
        return 1

    print("ok: beets-flask stack renders expected contract")
    return 0


def test_beets_flask_stack_contract() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())