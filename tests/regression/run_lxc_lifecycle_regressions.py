#!/usr/bin/env python3
"""Run the LXC lifecycle regression suite: fast feedback or full completion.

Thin runner only: it selects, launches, and reports the existing standalone
launchers. Every lifecycle scenario, input, and assertion stays in the
Ansible fixtures those launchers execute (see ADR 0007).

Fast path (default) — routine agent iteration:
  the semantic lifecycle facade matrix (planning, lifecycle intent,
  persistent destructive policy, result classification, controlled
  execution outcomes) plus the targeted lifecycle planning barrier.
Full path (--full) — completion checks before handoff:
  everything the fast path covers, plus the remaining lifecycle seams,
  including slow host-configuration idempotence sequencing and the real
  role-composition wiring regression.
Targeted path (--only FILENAME, repeatable) — focused remediation:
  registered launchers run sequentially in the supplied order. Add
  --fail-fast to stop scheduling selected launchers after the first failure.
  With --full, fail-fast still lets the concurrent fast launchers finish,
  then schedules no new launcher after a failure.

Both paths use controlled observations only: no live Proxmox, no vault
secrets, no machine-specific credentials.

Lifecycle planning keeps compilation and semantic plan construction in
independently scheduled Ansible plays. Fleet preflight and the all-target
planning barrier run only after their preceding target-local phase completes;
production execution remains linear. This preserves the full Ansible-native
scenario matrix while avoiding target-local planner task waves at the
targeted-set coordination seams (issue #44).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Sequence


TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parents[1]

# Both fast launchers are internally parallel and fully isolated (per-run
# temp state directories), so the fast path runs them concurrently.
FAST_SCRIPTS = (
    "test_lxc_lifecycle_decision.py",
    "test_lxc_lifecycle_planning_barrier.py",
)

# The rest of the lifecycle set runs sequentially: the host-configuration
# idempotence sequence and wiring regression are heavyweight, and sequential
# execution keeps their timing and failure attribution predictable.
FULL_ONLY_SCRIPTS = (
    "test_lifecycle_run_lock.py",
    "test_lxc_lifecycle_invalid_state.py",
    "test_lxc_lifecycle_guest_bootstrap_contract.py",
    "test_lxc_spec_contract.py",
    "test_lxc_spec_invalid_guest_bootstrap.py",
    "test_lxc_spec_layer_merge.py",
    "test_proxmox_lxc_provision_contract.py",
    "test_proxmox_lxc_lifecycle_configure_check_mode_absent.py",
    "test_proxmox_lxc_lifecycle_observation_status.py",
    "test_lxc_manual_ssh_recovery.py",
    "test_lxc_ssh_key_injector_identity_mismatch.py",
    "test_lxc_fleet_preflight.py",
    "test_proxmox_lxc_host_config_check_mode_missing_config.py",
    "test_proxmox_lxc_host_config_observation_failure.py",
    "test_proxmox_lxc_host_config_readiness_deadline.py",
    "test_lxc_docker_runtime_daemon_options.py",
    "test_lxc_nvidia_runtime_repository.py",
    "test_lxc_lifecycle_wiring.py",
    "test_proxmox_lxc_host_config_result.py",
    "test_hawser_standard_remote_default.py",
    "test_fixture_fact_cache_isolation.py",
)
REGISTERED_SCRIPTS = FAST_SCRIPTS + FULL_ONLY_SCRIPTS

LauncherResult = tuple[str, int, float, str]
Launcher = Callable[[str], LauncherResult]


def run_script(script: str) -> tuple[str, int, float, str]:
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(TESTS / script)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    wall = time.monotonic() - start
    return script, proc.returncode, wall, f"{proc.stdout}\n{proc.stderr}"


def report(result: tuple[str, int, float, str]) -> bool:
    script, returncode, wall, output = result
    status = "PASS" if returncode == 0 else "FAIL"
    print(f"{status}  {script}  ({wall:.1f}s)", flush=True)
    if returncode != 0:
        print(output, file=sys.stderr, flush=True)
    return returncode == 0


def run_regressions(
    *,
    full: bool,
    fail_fast: bool = False,
    only: Sequence[str] = (),
    launcher: Launcher = run_script,
) -> int:
    failed = []
    launched = 0
    if only:
        for script in only:
            launched += 1
            if not report(launcher(script)):
                failed.append(script)
                if fail_fast:
                    break
        if failed:
            print(f"failed: {', '.join(failed)}", file=sys.stderr)
            return 1
        print(f"ok: targeted lifecycle regression set passed ({launched} launchers)")
        return 0

    with ThreadPoolExecutor(max_workers=len(FAST_SCRIPTS)) as executor:
        for result in executor.map(launcher, FAST_SCRIPTS):
            launched += 1
            if not report(result):
                failed.append(result[0])
    if failed and fail_fast:
        print(f"failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    if full:
        for script in FULL_ONLY_SCRIPTS:
            launched += 1
            if not report(launcher(script)):
                failed.append(script)
                if fail_fast:
                    break

    if failed:
        print(f"failed: {', '.join(failed)}", file=sys.stderr)
        return 1

    label = "full lifecycle regression set" if full else "fast lifecycle feedback path"
    print(f"ok: {label} passed ({launched} launchers)")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    launcher: Launcher = run_script,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="run the complete lifecycle regression set, not just fast feedback",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FILENAME",
        help="run only this registered launcher (repeatable)",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop scheduling new launchers after a failure",
    )
    args = parser.parse_args(argv)
    if args.only and args.full:
        parser.error("--only cannot be combined with --full")
    unknown_scripts = [script for script in args.only if script not in REGISTERED_SCRIPTS]
    if unknown_scripts:
        parser.error(
            f"unknown lifecycle launcher: {unknown_scripts[0]}; "
            f"registered launchers: {', '.join(REGISTERED_SCRIPTS)}"
        )

    with tempfile.TemporaryDirectory(prefix="lxc-lifecycle-cache-") as temp_dir:
        cache_root = Path(temp_dir)
        previous_cache_plugin_connection = os.environ.get(
            "ANSIBLE_CACHE_PLUGIN_CONNECTION"
        )
        # Fixtures add fake hosts (e.g. s1_portal) that fact caching would
        # otherwise persist for up to the production TTL. Redirect the jsonfile
        # cache per run instead of forcing memory so within-run caching
        # semantics stay production-like while .ansible/cache stays untouched
        # (issue #89).
        os.environ["ANSIBLE_CACHE_PLUGIN_CONNECTION"] = str(cache_root / "fact-cache")
        try:
            return run_regressions(
                full=args.full,
                fail_fast=args.fail_fast,
                only=args.only,
                launcher=launcher,
            )
        finally:
            if previous_cache_plugin_connection is None:
                os.environ.pop("ANSIBLE_CACHE_PLUGIN_CONNECTION", None)
            else:
                os.environ["ANSIBLE_CACHE_PLUGIN_CONNECTION"] = (
                    previous_cache_plugin_connection
                )


if __name__ == "__main__":
    raise SystemExit(main())
