#!/usr/bin/env python3
"""Regression test for guest-bootstrap VMID/hostname identity validation."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from ansible_test_helper import ansible_playbook_command


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = (
    REPO_ROOT
    / "tests"
    / "regression"
    / "fixtures"
    / "lxc_ssh_key_injector_identity_mismatch_test.yml"
)
ANSIBLE_PLAYBOOK = ansible_playbook_command()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lxc-ssh-identity-mismatch-") as temp_dir:
        temp_root = Path(temp_dir)
        pct_log = temp_root / "pct-calls.log"
        pct = temp_root / "pct"
        pct.write_text(
            "#!/bin/sh\n"
            f"printf '%s %s\\n' \"$1\" \"$2\" >> '{pct_log}'\n"
            "case \"$1\" in\n"
            "  status)\n"
            "    [ \"${PCT_TEST_STATUS:-running}\" = absent ] && exit 1\n"
            "    echo \"status: ${PCT_TEST_STATUS:-running}\"\n"
            "    ;;\n"
            "  config) echo 'hostname: different-host' ;;\n"
            "  exec) echo 'CHANGED=1' ;;\n"
            "  *) exit 99 ;;\n"
            "esac\n"
        )
        pct.chmod(pct.stat().st_mode | stat.S_IXUSR)

        env = os.environ.copy()
        env["PATH"] = f"{temp_root}:{env['PATH']}"
        env["PCT_TEST_STATUS"] = "running"
        proc = subprocess.run(
            [*ANSIBLE_PLAYBOOK, str(PLAYBOOK)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
        )
        output = f"{proc.stdout}\n{proc.stderr}"

        if proc.returncode == 0:
            print("guest bootstrap accepted a mismatched container identity", file=sys.stderr)
            return 1

        if "does not match expected hostname" not in output:
            print("guest bootstrap failed without a clear identity mismatch", file=sys.stderr)
            print(output, file=sys.stderr)
            return 1

        calls = pct_log.read_text().splitlines()
        if calls != ["status 4206", "config 4206"]:
            print(f"identity mismatch reached an unexpected pct operation: {calls}", file=sys.stderr)
            return 1

        for runtime_state in ("stopped", "absent"):
            pct_log.write_text("")
            env["PCT_TEST_STATUS"] = runtime_state
            preserved = subprocess.run(
                [*ANSIBLE_PLAYBOOK, str(PLAYBOOK)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                env=env,
            )
            calls = pct_log.read_text().splitlines()
            if preserved.returncode != 0 or calls != ["status 4206"]:
                print(
                    f"{runtime_state} semantics changed unexpectedly: {calls}",
                    file=sys.stderr,
                )
                return 1

        pct_log.write_text("")
        env["PCT_TEST_STATUS"] = "running"
        check_mode = subprocess.run(
            [*ANSIBLE_PLAYBOOK, str(PLAYBOOK), "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
        )
        calls = pct_log.read_text().splitlines()
        if check_mode.returncode != 0 or calls != ["status 4206"]:
            print(f"check-mode semantics changed unexpectedly: {calls}", file=sys.stderr)
            return 1

    print("ok: guest bootstrap validates identity before mutation and preserves skip semantics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
