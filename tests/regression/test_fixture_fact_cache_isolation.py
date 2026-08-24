#!/usr/bin/env python3
"""Regression test: fixture runs cannot touch the live fact cache.

The lifecycle fixtures add fake inventory hosts (the s1_* convention) that
Ansible's jsonfile fact cache would persist into the operator's
.ansible/cache for up to the production TTL (issue #89). This launcher runs
the Hawser fixture through its test script and asserts the production
s1_portal cache entry is neither created nor modified.

The sentinel is s1_portal because ansible-core prefixes every add_host host
with s1_ when persisting facts; an unprotected Hawser run creates exactly
this entry with a workstation-only discovered_interpreter_python path.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS = REPO_ROOT / "tests" / "regression"
PRODUCTION_CACHE_ENTRY = REPO_ROOT / ".ansible" / "cache" / "s1_portal"
HAWSER_LAUNCHER = TESTS / "test_hawser_standard_remote_default.py"


def snapshot() -> str:
    if not PRODUCTION_CACHE_ENTRY.exists():
        return "absent"
    digest = hashlib.sha256(PRODUCTION_CACHE_ENTRY.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def main() -> int:
    before = snapshot()
    proc = subprocess.run(
        [sys.executable, str(HAWSER_LAUNCHER)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    output = f"{proc.stdout}\n{proc.stderr}"

    if proc.returncode != 0:
        print("Hawser fixture playbook failed unexpectedly", file=sys.stderr)
        print(output, file=sys.stderr)
        return 1

    after = snapshot()
    if after != before:
        print(
            "fixture run modified the production fact cache entry "
            f"{PRODUCTION_CACHE_ENTRY}: {before} -> {after}",
            file=sys.stderr,
        )
        print(output, file=sys.stderr)
        return 1

    print("ok: Hawser fixture leaves the production s1_portal fact cache entry unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
