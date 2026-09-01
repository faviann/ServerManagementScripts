#!/usr/bin/env python3
"""Regression test for the proxmox_lxc_provision contract boundary."""

from __future__ import annotations

import json
import os
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
    / "proxmox_lxc_provision_contract_test.yml"
)
ANSIBLE_PLAYBOOK = ansible_playbook_command()

PROXMOX_STUB = r'''#!/usr/bin/python
from __future__ import annotations

import json
import os

from ansible.module_utils.basic import AnsibleModule


module = AnsibleModule(
    argument_spec={
        "api_host": {"type": "str"},
        "api_port": {"type": "int"},
        "api_user": {"type": "str"},
        "api_token_id": {"type": "str"},
        "api_token_secret": {"type": "str", "no_log": True},
        "validate_certs": {"type": "bool"},
        "node": {"type": "str"},
        "vmid": {"type": "int"},
        "hostname": {"type": "str"},
        "ostemplate": {"type": "str"},
        "pool": {"type": "str"},
        "password": {"type": "str", "no_log": True},
        "pubkey": {"type": "str"},
        "description": {"type": "str"},
        "storage": {"type": "str"},
        "disk": {"type": "str"},
        "mount_volumes": {"type": "dict"},
        "cores": {"type": "int"},
        "cpus": {"type": "int"},
        "cpuunits": {"type": "int"},
        "memory": {"type": "int"},
        "swap": {"type": "int"},
        "netif": {"type": "dict"},
        "unprivileged": {"type": "bool"},
        "onboot": {"type": "bool"},
        "timezone": {"type": "str"},
        "nameserver": {"type": "str"},
        "searchdomain": {"type": "str"},
        "mounts": {"type": "dict"},
        "startup": {"type": "list"},
        "tags": {"type": "list"},
        "timeout": {"type": "int"},
        "ostype": {"type": "str"},
        "state": {"type": "str"},
        "update": {"type": "bool"},
    },
    supports_check_mode=True,
)

with open(os.environ["PROXMOX_STUB_CAPTURE"], "a", encoding="utf-8") as capture:
    capture.write(json.dumps(module.params, sort_keys=True) + "\n")

module.exit_json(changed=True)
'''


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="proxmox-lxc-provision-contract-") as temp_dir:
        temp_root = Path(temp_dir)
        module_dir = (
            temp_root
            / "ansible_collections"
            / "community"
            / "proxmox"
            / "plugins"
            / "modules"
        )
        module_dir.mkdir(parents=True)
        (module_dir / "proxmox.py").write_text(PROXMOX_STUB)

        capture_path = temp_root / "proxmox-calls.jsonl"
        env = os.environ.copy()
        env["ANSIBLE_COLLECTIONS_PATH"] = str(temp_root)
        env["PROXMOX_STUB_CAPTURE"] = str(capture_path)
        proc = subprocess.run(
            [*ANSIBLE_PLAYBOOK, str(PLAYBOOK)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
        )
        output = f"{proc.stdout}\n{proc.stderr}"

        if proc.returncode != 0:
            print("provision contract boundary failed unexpectedly", file=sys.stderr)
            print(output, file=sys.stderr)
            return 1

        calls = [json.loads(line) for line in capture_path.read_text().splitlines()]
        if len(calls) != 1:
            print(f"expected one contract-driven API call, got {len(calls)}", file=sys.stderr)
            return 1

        call = calls[0]
        expected = {
            "vmid": 4204,
            "hostname": "contract-host",
            "node": "contract-node",
            "memory": 1024,
            "state": "present",
        }
        if any(call[key] != value for key, value in expected.items()):
            print("API boundary did not receive the compiled contract values", file=sys.stderr)
            return 1

    print("ok: provisioning consumes only the compiled API contract slice")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
