#!/usr/bin/env python3
"""Regression test: fixture runs cannot touch the live fact cache.

Issue #89: the jsonfile fact cache persists fake fixture hosts (ansible-core
prefixes every add_host host with s1_) into the operator's .ansible/cache for
up to the production TTL, where a workstation-only
discovered_interpreter_python path breaks later live runs.

Three checks:
1. Guarded run: executing the Hawser launcher leaves the production
   s1_portal cache entry untouched in existence, content, and metadata.
2. Threat proof, initially-absent surrogate cache: the same Hawser launcher
   aimed at an empty sandbox cache creates s1_portal there, so check 1
   watches a demonstrated hazard rather than a hypothetical one.
3. Write detection, initially-present surrogate cache: deleting and
   recreating an entry with byte-identical content must still register as a
   modification, so comparisons include size/mtime/inode, not only content.
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
PRODUCTION_CACHE_ENTRY = REPO_ROOT / ".ansible" / "cache" / "s1_portal"
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


def run_hawser(cache_connection: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if cache_connection is not None:
        env["ANSIBLE_CACHE_PLUGIN_CONNECTION"] = cache_connection
    return subprocess.run(
        [sys.executable, str(HAWSER_LAUNCHER)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def main() -> int:
    failures: list[str] = []

    before = snapshot(PRODUCTION_CACHE_ENTRY)
    proc = run_hawser()
    output = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode != 0:
        failures.append("Hawser fixture playbook failed unexpectedly")
        failures.append(output)
    else:
        after = snapshot(PRODUCTION_CACHE_ENTRY)
        if after != before:
            failures.append(
                "isolated Hawser fixture run modified the production fact "
                f"cache entry {PRODUCTION_CACHE_ENTRY}: "
                f"{describe(before)} -> {describe(after)}"
            )
            failures.append(output)

    with tempfile.TemporaryDirectory(prefix="fact-cache-isolation-") as temp_root:
        surrogate_root = Path(temp_root)

        absent_cache = surrogate_root / "initially-absent" / "cache"
        proc_absent = run_hawser(str(absent_cache))
        absent_output = f"{proc_absent.stdout}\n{proc_absent.stderr}"
        if proc_absent.returncode != 0:
            failures.append("surrogate Hawser run failed unexpectedly")
            failures.append(absent_output)
        elif not snapshot(absent_cache / "s1_portal")[0]:
            failures.append(
                "an unprotected cache connection no longer persists s1_portal; "
                "the guarded production check may be watching a retired hazard"
            )

        present_entry = surrogate_root / "initially-present" / "cache" / "s1_portal"
        present_entry.parent.mkdir(parents=True)
        present_entry.write_bytes(b'{"sentinel":"issue-89"}\n')
        seeded = snapshot(present_entry)
        recreated = present_entry.with_name("s1_portal.recreated")
        recreated.write_bytes(present_entry.read_bytes())
        present_entry.unlink()
        recreated.rename(present_entry)
        if snapshot(present_entry) == seeded:
            failures.append(
                "detector missed a delete-and-recreate with identical bytes"
            )

    if failures:
        for line in failures:
            print(line, file=sys.stderr)
        return 1

    print(
        "ok: Hawser fixture leaves the production s1_portal fact cache entry "
        f"untouched ({describe(before)}); detector proven on absent and "
        "present surrogates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
