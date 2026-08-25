#!/usr/bin/env python3
"""Regression test: the Hawser fixture cannot touch the caller's fact cache.

Issue #89: the jsonfile fact cache persists fake fixture hosts (ansible-core
prefixes every add_host host with s1_) into the operator's .ansible/cache for
up to the production TTL, where a workstation-only
discovered_interpreter_python path breaks later live runs.

The launcher is exercised against production-cache surrogates with the target
entry both absent and present. In both cases an inherited cache connection must
be ignored, leaving the caller's cache unchanged.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS = REPO_ROOT / "tests" / "regression"
HAWSER_LAUNCHER = TESTS / "test_hawser_standard_remote_default.py"

EntryState = tuple[bool, int, int, int, str]


def snapshot(entry: Path) -> EntryState:
    if not entry.exists():
        return (False, -1, -1, -1, "")
    stat = entry.stat()
    digest = hashlib.sha256(entry.read_bytes()).hexdigest()
    return (True, stat.st_size, stat.st_mtime_ns, stat.st_ino, digest)


def describe(state: EntryState) -> str:
    exists, size, mtime_ns, inode, digest = state
    if not exists:
        return "absent"
    return f"size={size} mtime_ns={mtime_ns} inode={inode} sha256={digest}"


def run_hawser(cache_connection: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ANSIBLE_CACHE_PLUGIN_CONNECTION"] = str(cache_connection)
    return subprocess.run(
        [sys.executable, str(HAWSER_LAUNCHER)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def assert_untouched(
    cache_entry: Path, expected: EntryState, failures: list[str]
) -> None:
    proc = run_hawser(cache_entry.parent)
    output = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode != 0:
        failures.append("Hawser fixture playbook failed unexpectedly")
        failures.append(output)
        return

    actual = snapshot(cache_entry)
    if actual != expected:
        failures.append(
            "isolated Hawser fixture run modified the inherited production "
            f"fact cache entry {cache_entry}: "
            f"{describe(expected)} -> {describe(actual)}"
        )
        failures.append(output)


def main() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="fact-cache-isolation-") as temp_root:
        production_cache = Path(temp_root) / "production-cache"
        cache_entry = production_cache / "s1_portal"

        assert_untouched(cache_entry, snapshot(cache_entry), failures)

        production_cache.mkdir(exist_ok=True)
        cache_entry.write_bytes(b'{"sentinel":"issue-89"}\n')
        assert_untouched(cache_entry, snapshot(cache_entry), failures)

    if failures:
        for line in failures:
            print(line, file=sys.stderr)
        return 1

    print(
        "ok: Hawser fixture ignores inherited production cache connections "
        "when s1_portal is initially absent or present"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
