#!/usr/bin/env python3
"""Static inventory checks for the workstation LXC contract."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


class WorkstationInventoryTests(unittest.TestCase):
    def test_workstation_inventory_contract(self) -> None:
        inventory = load_yaml(REPO_ROOT / "inventory/hosts.yml")
        workstation_vars = load_yaml(REPO_ROOT / "inventory/host_vars/workstation.yml")
        all_children = inventory["all"]["children"]
        overrides = workstation_vars["proxmox_lxc_overrides"]

        self.assertIn("workstation", all_children["tier_large"]["hosts"])
        self.assertIn("workstation", all_children["cap_docker"]["hosts"])
        self.assertNotIn("workstation", all_children["cap_wireguard"]["hosts"])

        self.assertEqual(workstation_vars["workstation_enabled"], True)
        self.assertNotIn("docker_user", workstation_vars)
        self.assertNotIn("docker_uid", workstation_vars)
        self.assertNotIn("docker_gid", workstation_vars)
        cap_docker_vars = load_yaml(REPO_ROOT / "inventory/group_vars/cap_docker/vars.yml")
        self.assertEqual(cap_docker_vars["docker_user"], "faviann")
        self.assertEqual(cap_docker_vars["docker_uid"], 1000)
        self.assertEqual(cap_docker_vars["docker_gid"], 1000)
        lxcs_vars = load_yaml(REPO_ROOT / "inventory/group_vars/lxcs/vars.yml")
        self.assertEqual(lxcs_vars["lxc_github_users"], ["faviann"])
        self.assertEqual(workstation_vars["docker_agents_enabled"], False)
        self.assertEqual(workstation_vars["traefik_kop_enabled"], False)
        self.assertEqual(workstation_vars["lxc_hwaddr"], "BC:24:11:57:80:06")
        self.assertEqual(overrides["vmid"], 306)
        self.assertEqual(overrides["hostname"], "workstation")
        self.assertEqual(overrides["cores"], 16)
        self.assertEqual(overrides["memory"], 32768)
        self.assertEqual(overrides["disk"], "128")
        self.assertEqual(
            overrides["description"],
            "Persistent remote coding workstation managed via Ansible",
        )
        self.assertEqual(overrides["tags"], ["ansible", "workstation", "development"])

    def test_workstation_persistent_home_includes_agent_state(self) -> None:
        defaults = load_yaml(
            REPO_ROOT / "playbooks/roles/config/lxc_workstation_baseline/defaults/main.yml"
        )
        links = defaults["workstation_persistent_home_links"]

        self.assertIn(
            {
                "name": "agents",
                "type": "bind_mount",
                "path": "{{ workstation_home }}/.agents",
                "target": "{{ workstation_persistent_home_root }}/.agents",
                "mode": "0700",
            },
            links,
        )

        # Collie regenerates its user unit at ~/.config/systemd/user/collie.service, and herdr
        # rebuilds its agent-detection cache under ~/.local/state/herdr. Persisting either would
        # pin a stale copy, so both stay outside the required persistence contract.
        excluded_fragments = (
            "systemd/user",
            "collie.service",
            ".local/state/herdr",
            "agent-detection",
        )
        for link in links:
            for field in ("path", "target"):
                value = link[field]
                for fragment in excluded_fragments:
                    self.assertNotIn(
                        fragment,
                        value,
                        msg=(
                            f"persistent home entry {link['name']!r} {field} must not reference "
                            f"regenerable state {fragment!r}"
                        ),
                    )


if __name__ == "__main__":
    unittest.main()
