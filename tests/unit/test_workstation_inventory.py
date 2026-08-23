#!/usr/bin/env python3
"""Static inventory checks for the workstation LXC contract."""

from __future__ import annotations

import posixpath
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSTATION_HOME = "{{ workstation_home }}"


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def at_or_under(path: str, ancestor: str) -> bool:
    """True when path is ancestor itself or lives beneath it.

    Compares whole path components so `.../herdr` and `.../herdr-x` stay distinct.
    """
    path = posixpath.normpath(path)
    ancestor = posixpath.normpath(ancestor)
    return path == ancestor or path.startswith(ancestor + "/")


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

    def effective_persistent_home_links(self) -> list[dict]:
        """Resolve the list the workstation host actually deploys.

        The role default applies unless inventory/host_vars/workstation.yml overrides it,
        so the assertions below follow an override if one is ever added.
        """
        workstation_vars = load_yaml(REPO_ROOT / "inventory/host_vars/workstation.yml")
        if "workstation_persistent_home_links" in workstation_vars:
            return workstation_vars["workstation_persistent_home_links"]
        defaults = load_yaml(
            REPO_ROOT / "playbooks/roles/config/lxc_workstation_baseline/defaults/main.yml"
        )
        return defaults["workstation_persistent_home_links"]

    def test_workstation_persistent_home_includes_agent_state(self) -> None:
        links = self.effective_persistent_home_links()

        for expected in (
            {
                "name": "agents",
                "type": "bind_mount",
                "path": "{{ workstation_home }}/.agents",
                "target": "{{ workstation_persistent_home_root }}/.agents",
                "mode": "0700",
            },
            {
                "name": "herdr",
                "type": "bind_mount",
                "path": "{{ workstation_home }}/.config/herdr",
                "target": "{{ workstation_persistent_home_root }}/.config/herdr",
                "mode": "0700",
            },
            {
                "name": "collie_state",
                "type": "bind_mount",
                "path": "{{ workstation_home }}/.local/state/collie",
                "target": "{{ workstation_persistent_home_root }}/.local/state/collie",
                "mode": "0700",
            },
            {
                "name": "moraine",
                "type": "bind_mount",
                "path": "{{ workstation_home }}/.moraine",
                "target": "{{ workstation_persistent_home_root }}/.moraine",
                "mode": "0700",
            },
        ):
            self.assertIn(expected, links)

    def test_workstation_persistent_home_excludes_regenerable_state(self) -> None:
        # Two concrete paths must stay ephemeral: ~/.config/systemd/user/collie.service is
        # regenerated by Collie and embeds checkout-specific paths, and
        # ~/.local/state/herdr/agent-detection is rebuilt by herdr. Persisting either pins a
        # stale copy that survives a rebuild and then contradicts the running checkout.
        # Mounting ~/.local/state itself is the realistic way the herdr cache gets dragged in,
        # so the parent is rejected too — only ~/.local/state/collie is in the contract.
        forbidden_trees = (
            f"{WORKSTATION_HOME}/.config/systemd/user",
            f"{WORKSTATION_HOME}/.local/state/herdr",
        )
        forbidden_exact = f"{WORKSTATION_HOME}/.local/state"

        for link in self.effective_persistent_home_links():
            path = link["path"]
            for tree in forbidden_trees:
                self.assertFalse(
                    at_or_under(path, tree),
                    msg=(
                        f"persistent home entry {link['name']!r} path {path!r} must not persist "
                        f"regenerable state under {tree!r}"
                    ),
                )
            self.assertNotEqual(
                posixpath.normpath(path),
                posixpath.normpath(forbidden_exact),
                msg=(
                    f"persistent home entry {link['name']!r} must mount a specific directory "
                    f"under {forbidden_exact!r}, not the parent"
                ),
            )


if __name__ == "__main__":
    unittest.main()
