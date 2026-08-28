#!/usr/bin/env python3
"""Regression test for minimal manual LXC SSH recovery."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "playbooks" / "add-ssh-keys-to-lxcs.yml"
ANSIBLE_PLAYBOOK = "uv run --locked ansible-playbook".split()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lxc-manual-ssh-recovery-") as temp_dir:
        temp_root = Path(temp_dir)
        public_key = temp_root / "controller.pub"
        public_key.write_text("ssh-ed25519 AAAARECOVERY recovery@test\n")

        pct_log = temp_root / "pct-calls.log"
        pct_exec_count = temp_root / "pct-exec-count"
        pct = temp_root / "pct"
        pct.write_text(
            "#!/bin/sh\n"
            f"printf '%s %s\\n' \"$1\" \"$2\" >> '{pct_log}'\n"
            "case \"$1\" in\n"
            "  status) echo 'status: running' ;;\n"
            "  config) echo 'hostname: recovery-host' ;;\n"
            "  exec)\n"
            f"    if [ ! -f '{pct_exec_count}' ]; then\n"
            f"      : > '{pct_exec_count}'\n"
            "      echo 'CHANGED=1'\n"
            "    else\n"
            "      echo '1'\n"
            "    fi\n"
            "    ;;\n"
            "  *) exit 99 ;;\n"
            "esac\n"
        )
        pct.chmod(pct.stat().st_mode | stat.S_IXUSR)

        inventory = temp_root / "inventory.yml"
        inventory.write_text(
            "all:\n"
            "  children:\n"
            "    lxcs:\n"
            "      hosts:\n"
            "        recovery-host:\n"
            "          ansible_connection: local\n"
            "          ansible_python_interpreter: '{{ ansible_playbook_python }}'\n"
            "          proxmox_pct_delegate_host: localhost\n"
            "          proxmox_api_host: localhost\n"
            f"          proxmox_lxc_controller_pubkey_path: '{public_key}'\n"
            "          proxmox_lxc_overrides:\n"
            "            vmid: 4201\n"
            "            hostname: recovery-host\n"
            "          proxmox_default_storage: ''\n"
            "          proxmox_lxc_global_defaults:\n"
            "            node: ''\n"
            "            ostemplate: ''\n"
            "          proxmox_lxc_group_defaults:\n"
            "            cores: 0\n"
            "            memory: 1\n"
            "            disk: ''\n"
            "            netif: {}\n"
            "    cap_docker:\n"
            "      hosts:\n"
            "        unrelated-invalid-host:\n"
            "          docker_agents_enabled: true\n"
            "          traefik_kop_enabled: true\n"
        )

        env = os.environ.copy()
        env["PATH"] = f"{temp_root}:{env['PATH']}"
        env["HOMELAB_IAC_LIFECYCLE_WRAPPER"] = "1"
        proc = subprocess.run(
            [
                *ANSIBLE_PLAYBOOK,
                "-i",
                str(inventory),
                str(PLAYBOOK),
                "--limit",
                "recovery-host",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
        )

        output = f"{proc.stdout}\n{proc.stderr}"
        if proc.returncode != 0:
            print("manual SSH recovery playbook failed unexpectedly", file=sys.stderr)
            print(output, file=sys.stderr)
            return 1

        calls = pct_log.read_text().splitlines()
        if calls != ["status 4201", "config 4201", "exec 4201", "exec 4201"]:
            print(f"unexpected pct calls: {calls}", file=sys.stderr)
            return 1

    print("ok: manual SSH recovery ignores unrelated invalid desired infrastructure")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
