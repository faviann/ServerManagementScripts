#!/usr/bin/env python3
"""Regression test for authoritative Proxmox LXC host configuration."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ansible_test_helper import ansible_playbook_command


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "tests" / "regression" / "fixtures" / "proxmox_lxc_host_config_test.yml"
ANSIBLE_PLAYBOOK = ansible_playbook_command()
TEST_LOCALES = ("C.UTF-8", "en_US.UTF-8")


def main() -> int:
    for locale_name in TEST_LOCALES:
        env = os.environ.copy()
        env.update(LANG=locale_name, LC_ALL=locale_name)

        with tempfile.TemporaryDirectory(prefix="proxmox-lxc-host-config-") as temp_root:
            proc = subprocess.run(
                [*ANSIBLE_PLAYBOOK, str(PLAYBOOK), "-e", f"temp_root={temp_root}"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                env=env,
            )

        output = f"{proc.stdout}\n{proc.stderr}"

        if proc.returncode != 0:
            print(
                f"playbook failed unexpectedly under {locale_name}", file=sys.stderr
            )
            print(output, file=sys.stderr)
            return 1

    print(
        "ok: proxmox_lxc_host_config is authoritative and reorder-safe "
        f"under {', '.join(TEST_LOCALES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
