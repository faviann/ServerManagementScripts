#!/usr/bin/env python3
"""Regression test for the Hawser Standard remote-host default configuration."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "tests" / "regression" / "fixtures" / "hawser_standard_remote_default_test.yml"
ANSIBLE_PLAYBOOK = "uv run --locked ansible-playbook".split()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hawser-standard-remote-default-") as temp_root:
        env = os.environ.copy()
        # Fake fixture hosts must not persist into the operator's fact cache
        # (issue #89); honor an inherited connection so aggregate runners
        # share their sandbox instead of stacking temporary directories.
        env.setdefault(
            "ANSIBLE_CACHE_PLUGIN_CONNECTION", str(Path(temp_root) / "fact-cache")
        )
        proc = subprocess.run(
            [*ANSIBLE_PLAYBOOK, str(PLAYBOOK), "-e", f"temp_root={temp_root}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
        )

    output = f"{proc.stdout}\n{proc.stderr}"

    if proc.returncode != 0:
        print("playbook failed unexpectedly", file=sys.stderr)
        print(output, file=sys.stderr)
        return 1

    print("ok: Hawser renders by default on remote Docker hosts and stays disabled on portal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())