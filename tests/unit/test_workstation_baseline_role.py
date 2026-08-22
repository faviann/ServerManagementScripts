#!/usr/bin/env python3
"""Contract tests for the workstation baseline role."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def flatten_tasks(tasks: list[dict[str, object]]) -> list[dict[str, object]]:
    flat_tasks: list[dict[str, object]] = []
    for task in tasks:
        flat_tasks.append(task)
        for child_key in ("block", "always", "rescue"):
            child_tasks = task.get(child_key, [])
            if isinstance(child_tasks, list):
                flat_tasks.extend(flatten_tasks(child_tasks))
    return flat_tasks


class WorkstationBaselineRoleTests(unittest.TestCase):
    def test_role_defaults_contract(self) -> None:
        defaults = load_yaml(REPO_ROOT / "playbooks/roles/config/lxc_workstation_baseline/defaults/main.yml")

        self.assertEqual(defaults["workstation_username"], "{{ docker_user }}")
        self.assertEqual(defaults["workstation_uid"], "{{ docker_uid }}")
        self.assertEqual(defaults["workstation_gid"], "{{ docker_gid }}")
        self.assertEqual(defaults["workstation_home"], "/home/{{ workstation_username }}")
        self.assertFalse(defaults["workstation_origin_firewall_enabled"])
        self.assertEqual(defaults["workstation_origin_firewall_protected_ports"], [])
        self.assertEqual(defaults["workstation_origin_firewall_allowed_hosts"], [])
        self.assertEqual(defaults["workstation_origin_firewall_nft_dir"], "/etc/nftables.d")
        self.assertEqual(
            defaults["workstation_origin_firewall_nft_path"],
            "{{ workstation_origin_firewall_nft_dir }}/workstation-origin-firewall.nft",
        )
        self.assertEqual(
            defaults["workstation_origin_firewall_service_path"],
            "/etc/systemd/system/workstation-origin-firewall.service",
        )
        self.assertFalse(defaults["workstation_persistent_home_enabled"])
        self.assertEqual(defaults["workstation_persistent_home_root"], "/ephemeral/workstation/home")
        self.assertEqual(defaults["workstation_persistent_home_mount_state"], "mounted")
        self.assertEqual(defaults["workstation_persistent_home_fstab_path"], "/etc/fstab")
        self.assertEqual(
            defaults["workstation_persistent_home_links"],
            [
                {
                    "name": "claude",
                    "type": "bind_mount",
                    "path": "{{ workstation_home }}/.claude",
                    "target": "{{ workstation_persistent_home_root }}/.claude",
                    "mode": "0700",
                },
                {
                    "name": "codex",
                    "type": "bind_mount",
                    "path": "{{ workstation_home }}/.codex",
                    "target": "{{ workstation_persistent_home_root }}/.codex",
                    "mode": "0700",
                },
                {
                    "name": "agents",
                    "type": "bind_mount",
                    "path": "{{ workstation_home }}/.agents",
                    "target": "{{ workstation_persistent_home_root }}/.agents",
                    "mode": "0700",
                },
                {
                    "name": "pi",
                    "type": "bind_mount",
                    "path": "{{ workstation_home }}/.pi",
                    "target": "{{ workstation_persistent_home_root }}/.pi",
                    "mode": "0700",
                },
                {
                    "name": "omp",
                    "type": "bind_mount",
                    "path": "{{ workstation_home }}/.omp",
                    "target": "{{ workstation_persistent_home_root }}/.omp",
                    "mode": "0700",
                },
                {
                    "name": "opencode_config",
                    "type": "bind_mount",
                    "path": "{{ workstation_home }}/.config/opencode",
                    "target": "{{ workstation_persistent_home_root }}/.config/opencode",
                    "mode": "0700",
                },
                {
                    "name": "opencode_data",
                    "type": "bind_mount",
                    "path": "{{ workstation_home }}/.local/share/opencode",
                    "target": "{{ workstation_persistent_home_root }}/.local/share/opencode",
                    "mode": "0700",
                },
                {
                    "name": "opencode_state",
                    "type": "bind_mount",
                    "path": "{{ workstation_home }}/.local/state/opencode",
                    "target": "{{ workstation_persistent_home_root }}/.local/state/opencode",
                    "mode": "0700",
                },
                {
                    "name": "agent_of_empires",
                    "type": "bind_mount",
                    "path": "{{ workstation_home }}/.config/agent-of-empires",
                    "target": "{{ workstation_persistent_home_root }}/.config/agent-of-empires",
                    "mode": "0700",
                },
                {
                    "name": "hermes",
                    "type": "bind_mount",
                    "path": "{{ workstation_home }}/.hermes",
                    "target": "{{ workstation_persistent_home_root }}/.hermes",
                    "mode": "0700",
                },
                {
                    "name": "openclaw",
                    "type": "bind_mount",
                    "path": "{{ workstation_home }}/.openclaw",
                    "target": "{{ workstation_persistent_home_root }}/.openclaw",
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
                    "name": "repos",
                    "type": "bind_mount",
                    "path": "{{ workstation_home }}/repos",
                    "target": "{{ workstation_persistent_home_root }}/repos",
                    "mode": "0755",
                },
            ],
        )
        self.assertNotIn("workstation_agent_state_enabled", defaults)
        self.assertNotIn("workstation_agent_state_root", defaults)
        self.assertNotIn("workstation_agent_state_links", defaults)
        self.assertNotIn("workstation_github_known_host_name", defaults)
        self.assertNotIn("workstation_github_ssh_private_key_path", defaults)
        self.assertNotIn("workstation_github_register_public_key", defaults)
        expected_apt_packages = {
            "tmux",
            "mosh",
            "wget",
            "git",
            "htop",
            "tree",
            "zip",
            "unzip",
            "build-essential",
            "pkg-config",
            "python3-venv",
            "python3-pip",
            "bubblewrap",
        }
        self.assertTrue(
            expected_apt_packages.issubset(set(defaults["workstation_packages"])),
            msg="workstation_packages is missing expected baseline OS tools",
        )
        self.assertTrue(
            {
                "gh",
                "jq",
                "ripgrep",
                "fd-find",
                "fzf",
                "nodejs",
                "npm",
                "pipx",
            }.isdisjoint(set(defaults["workstation_packages"])),
            msg="Home Manager-owned tools must not be installed through apt",
        )
        self.assertEqual(
            defaults["workstation_setup_marker_path"],
            "{{ workstation_home }}/.local/state/workstation-setup/complete",
        )
        self.assertEqual(defaults["workstation_setup_version"], "2")
        self.assertEqual(defaults["workstation_setup_bin_path"], "/usr/local/bin/workstation-setup")
        self.assertEqual(defaults["workstation_setup_profile_hook_path"], "/etc/profile.d/workstation-setup.sh")
        self.assertEqual(
            defaults["workstation_update_agent_tools_source_path"],
            "{{ workstation_home }}/.local/share/chezmoi/dot_local/bin/executable_update-agent-tools",
        )
        self.assertEqual(
            defaults["workstation_update_agent_tools_bin_path"],
            "{{ workstation_home }}/.local/bin/update-agent-tools",
        )
        self.assertFalse(defaults["workstation_update_agent_tools_requires_bitwarden"])
        self.assertEqual(
            defaults["workstation_update_source_path"],
            "{{ workstation_home }}/.local/share/chezmoi/dot_local/bin/executable_workstation-update",
        )
        self.assertEqual(
            defaults["workstation_update_bin_path"],
            "{{ workstation_home }}/.local/bin/workstation-update",
        )
        self.assertFalse(defaults["workstation_update_requires_bitwarden"])
        self.assertNotIn("workstation_bootstrap_unattended", defaults)
        self.assertNotIn("workstation_bootstrap_run_dir", defaults)
        self.assertNotIn("workstation_bootstrap_env_path", defaults)
        self.assertNotIn("workstation_bootstrap_marker_path", defaults)
        self.assertNotIn("workstation_bootstrap_controller_env", defaults)
        self.assertNotIn("workstation_mise_install_url", defaults)
        self.assertNotIn("workstation_mise_bin", defaults)
        self.assertNotIn("workstation_mise_shims_dir", defaults)
        self.assertNotIn("workstation_mise_tools", defaults)
        self.assertEqual(defaults["workstation_dotfiles_repo_url"], "https://github.com/faviann/dotfiles.git")
        self.assertEqual(defaults["workstation_github_cli_token_item"], "dotfiles/github-cli-token")
        self.assertTrue(defaults["workstation_nix_install_enabled"])
        self.assertTrue(defaults["workstation_enable_linger"])
        self.assertEqual(defaults["workstation_nix_install_url"], "https://install.determinate.systems/nix")
        self.assertEqual(
            defaults["workstation_home_manager_flake_ref"],
            "{{ workstation_home }}/.local/share/chezmoi#workstation",
        )
        self.assertEqual(
            defaults["workstation_bw_release_api_url"],
            "https://api.github.com/repos/bitwarden/clients/releases/tags/cli-v{{ workstation_bw_version }}",
        )
        self.assertEqual(defaults["workstation_bw_bin_path"], "/usr/local/bin/bw")

    def test_lifecycle_wires_workstation_baseline_role_once(self) -> None:
        tasks = load_yaml(REPO_ROOT / "playbooks/roles/provisioning/proxmox_lxc_lifecycle/tasks/configure.yml")
        flat_tasks = []
        for task in tasks:
            flat_tasks.append(task)
            flat_tasks.extend(task.get("block", []))
        matching_tasks = [task for task in flat_tasks if task.get("name") == "Configure workstation baseline"]

        self.assertEqual(len(matching_tasks), 1)

        task = matching_tasks[0]
        include_role = next(
            (value for key, value in task.items() if key.endswith("include_role")),
            None,
        )
        self.assertIsNotNone(include_role)
        self.assertEqual(include_role["name"], "config/lxc_workstation_baseline")

        when_value = task.get("when")
        if isinstance(when_value, list):
            when_text = " ".join(str(item) for item in when_value)
        else:
            when_text = str(when_value)
        self.assertIn("workstation_enabled | default(false)", when_text)

    def test_workstation_inventory_opt_in_contract(self) -> None:
        workstation_vars = load_yaml(REPO_ROOT / "inventory/host_vars/workstation.yml")

        self.assertTrue(workstation_vars["workstation_enabled"])
        self.assertTrue(workstation_vars["workstation_origin_firewall_enabled"])
        self.assertEqual(workstation_vars["workstation_origin_firewall_protected_ports"], [4001, 9119, 18789, 8788])
        self.assertEqual(workstation_vars["workstation_origin_firewall_allowed_hosts"], ["portal"])
        self.assertTrue(workstation_vars["workstation_persistent_home_enabled"])
        self.assertNotIn("workstation_agent_state_enabled", workstation_vars)

    def test_role_argument_specs_contract(self) -> None:
        specs = load_yaml(REPO_ROOT / "playbooks/roles/config/lxc_workstation_baseline/meta/argument_specs.yml")
        options = specs["argument_specs"]["main"]["options"]

        self.assertEqual(options["workstation_origin_firewall_enabled"]["type"], "bool")
        self.assertFalse(options["workstation_origin_firewall_enabled"]["required"])
        self.assertEqual(options["workstation_origin_firewall_protected_ports"]["type"], "list")
        self.assertEqual(options["workstation_origin_firewall_protected_ports"]["elements"], "int")
        self.assertFalse(options["workstation_origin_firewall_protected_ports"]["required"])
        self.assertEqual(options["workstation_origin_firewall_allowed_hosts"]["type"], "list")
        self.assertEqual(options["workstation_origin_firewall_allowed_hosts"]["elements"], "str")
        self.assertFalse(options["workstation_origin_firewall_allowed_hosts"]["required"])
        self.assertEqual(options["workstation_persistent_home_enabled"]["type"], "bool")
        self.assertFalse(options["workstation_persistent_home_enabled"]["required"])
        self.assertEqual(options["workstation_persistent_home_root"]["type"], "str")
        self.assertFalse(options["workstation_persistent_home_root"]["required"])
        self.assertEqual(options["workstation_persistent_home_links"]["type"], "list")
        self.assertEqual(options["workstation_persistent_home_links"]["elements"], "dict")
        self.assertFalse(options["workstation_persistent_home_links"]["required"])
        self.assertEqual(options["workstation_persistent_home_mount_state"]["type"], "str")
        self.assertFalse(options["workstation_persistent_home_mount_state"]["required"])
        self.assertEqual(options["workstation_persistent_home_fstab_path"]["type"], "str")
        self.assertFalse(options["workstation_persistent_home_fstab_path"]["required"])
        self.assertEqual(options["workstation_setup_marker_path"]["type"], "str")
        self.assertEqual(options["workstation_setup_bin_path"]["type"], "str")
        self.assertEqual(options["workstation_setup_profile_hook_path"]["type"], "str")
        self.assertEqual(options["workstation_update_agent_tools_source_path"]["type"], "str")
        self.assertEqual(options["workstation_update_agent_tools_bin_path"]["type"], "str")
        self.assertEqual(options["workstation_update_agent_tools_requires_bitwarden"]["type"], "bool")
        self.assertEqual(options["workstation_update_source_path"]["type"], "str")
        self.assertEqual(options["workstation_update_bin_path"]["type"], "str")
        self.assertEqual(options["workstation_update_requires_bitwarden"]["type"], "bool")
        self.assertEqual(options["workstation_dotfiles_repo_url"]["type"], "str")
        self.assertEqual(options["workstation_github_cli_token_item"]["type"], "str")
        self.assertEqual(options["workstation_nix_install_enabled"]["type"], "bool")
        self.assertEqual(options["workstation_nix_install_url"]["type"], "str")
        self.assertEqual(options["workstation_home_manager_flake_ref"]["type"], "str")
        self.assertEqual(options["workstation_enable_linger"]["type"], "bool")
        self.assertEqual(options["workstation_bw_release_api_url"]["type"], "str")
        self.assertEqual(options["workstation_bw_bin_path"]["type"], "str")
        self.assertEqual(options["workstation_system_bin_owner"]["type"], "str")
        self.assertEqual(options["workstation_system_bin_group"]["type"], "str")
        self.assertNotIn("workstation_bootstrap_unattended", options)
        self.assertNotIn("workstation_bootstrap_run_dir", options)
        self.assertNotIn("workstation_bootstrap_env_path", options)
        self.assertNotIn("workstation_bootstrap_marker_path", options)
        self.assertNotIn("workstation_bootstrap_controller_env", options)
        self.assertNotIn("workstation_mise_install_url", options)
        self.assertNotIn("workstation_mise_bin", options)
        self.assertNotIn("workstation_mise_shims_dir", options)
        self.assertNotIn("workstation_mise_tools", options)
        self.assertNotIn("workstation_agent_state_enabled", options)
        self.assertNotIn("workstation_agent_state_root", options)
        self.assertNotIn("workstation_agent_state_links", options)

    def test_role_tasks_contract(self) -> None:
        tasks = load_yaml(REPO_ROOT / "playbooks/roles/config/lxc_workstation_baseline/tasks/main.yml")
        task_names = [t.get("name") for t in tasks]
        self.assertIn("Install workstation baseline packages", task_names)
        self.assertIn("Configure workstation origin firewall", task_names)
        self.assertNotIn("Configure AoE LAN proxy firewall", task_names)
        self.assertIn("Configure workstation persistent home mounts", task_names)
        self.assertIn("Configure GitHub SSH keys", task_names)
        self.assertIn("Install chezmoi", task_names, "missing chezmoi install task")
        self.assertIn("Install Bitwarden CLI", task_names, "missing bw CLI install task")
        self.assertIn("Install Determinate Nix", task_names, "missing Nix install task")
        self.assertIn("Enable workstation user lingering", task_names, "missing linger task")
        self.assertIn("Install workstation setup command", task_names, "missing setup command task")
        self.assertIn("Install workstation setup login hook", task_names, "missing setup login hook task")
        self.assertNotIn("Install mise", task_names)
        self.assertNotIn("Install workstation bootstrap script", task_names)
        self.assertNotIn("Run unattended workstation bootstrap", task_names)

        removed_task_names = {
            "Fetch GitHub SSH host key on controller",
            "Ensure github.com known_hosts entry exists",
            "Ensure workstation GitHub known_hosts permissions",
            "Generate workstation GitHub SSH keypair",
            "Ensure workstation GitHub SSH key ownership",
            "Read workstation GitHub SSH public key",
            "Set workstation GitHub SSH public key fact",
            "Verify GitHub CLI auth is available on controller",
            "List registered GitHub SSH public keys on controller",
            "Register workstation GitHub SSH public key on controller",
        }
        self.assertTrue(
            removed_task_names.isdisjoint(task_names),
            msg="workstation baseline still contains removed outbound GitHub SSH identity tasks",
        )

        # Debian's locale-gen generates only the uncommented entries of
        # /etc/locale.gen and can ignore a locale passed as an argument, so it
        # exits 0 having generated nothing. The entry must be enabled first.
        self.assertIn("Enable the en_US.UTF-8 locale entry", task_names)
        locale_entry = next(
            t for t in tasks if t.get("name") == "Enable the en_US.UTF-8 locale entry"
        )
        self.assertEqual(locale_entry["ansible.builtin.lineinfile"]["path"], "/etc/locale.gen")
        self.assertEqual(locale_entry["ansible.builtin.lineinfile"]["line"], "en_US.UTF-8 UTF-8")
        locale_gen = next(t for t in tasks if t.get("name") == "Generate enabled locales")
        locale_gen_cmd = locale_gen["ansible.builtin.shell"]["cmd"]
        self.assertNotIn(
            "locale-gen en_US",
            locale_gen_cmd,
            msg="locale-gen must not rely on a locale argument; it is ignored on Debian images",
        )
        self.assertIn("locale-gen", locale_gen_cmd)
        self.assertLess(
            task_names.index("Enable the en_US.UTF-8 locale entry"),
            task_names.index("Generate enabled locales"),
        )

        rendered_tasks = yaml.safe_dump(tasks, sort_keys=True)
        self.assertIn("origin_firewall.yml", rendered_tasks)
        self.assertNotIn("aoe_proxy_firewall.yml", rendered_tasks)
        self.assertIn("bitwarden_cli.yml", rendered_tasks)
        self.assertIn("nix.yml", rendered_tasks)
        self.assertIn("loginctl", rendered_tasks)
        self.assertIn("workstation-setup.sh.j2", rendered_tasks)
        self.assertIn("workstation-setup-profile.sh.j2", rendered_tasks)
        self.assertNotIn("mise.yml", rendered_tasks)
        self.assertNotIn("bootstrap.yml", rendered_tasks)
        self.assertNotIn("workstation_bootstrap_unattended", rendered_tasks)
        self.assertNotIn("/run/workstation-bootstrap", rendered_tasks)
        self.assertNotIn("BW_CLIENTID", rendered_tasks)
        self.assertNotIn("BW_CLIENTSECRET", rendered_tasks)
        self.assertNotIn("npm install -g @bitwarden/cli", rendered_tasks)
        self.assertFalse(
            (REPO_ROOT / "playbooks/roles/config/lxc_workstation_baseline/tasks/bootstrap.yml").exists()
        )
        setup_template = (
            REPO_ROOT / "playbooks/roles/config/lxc_workstation_baseline/templates/workstation-setup.sh.j2"
        ).read_text(encoding="utf-8")
        self.assertIn("bw status", setup_template)
        self.assertIn("Bitwarden is unauthenticated", setup_template)
        self.assertIn("Bitwarden is locked", setup_template)
        self.assertIn("Bitwarden account password", setup_template)
        self.assertIn("bw login", setup_template)
        self.assertIn("bw unlock --raw", setup_template)
        self.assertIn("chezmoi init --apply", setup_template)
        self.assertIn("chezmoi update", setup_template)
        self.assertIn("home-manager switch -b workstation-setup-backup --flake", setup_template)
        self.assertIn("nix --version", setup_template)
        self.assertIn("home-manager --version", setup_template)
        self.assertIn("hermes version", setup_template)
        self.assertIn("gh auth login --hostname github.com --with-token", setup_template)
        self.assertIn("gh api user", setup_template)
        self.assertIn("git@github.com", setup_template)
        self.assertIn("workstation_update_agent_tools_source_path", setup_template)
        self.assertIn("workstation_update_agent_tools_bin_path", setup_template)
        self.assertIn("workstation_update_source_path", setup_template)
        self.assertIn("workstation_update_bin_path", setup_template)
        self.assertIn("environment healthy", setup_template)
        self.assertIn("environment repaired and ready", setup_template)
        self.assertNotIn("mise", setup_template)
        self.assertNotIn("parse_env_file", setup_template)
        self.assertNotIn("/run/workstation-bootstrap", setup_template)
        self.assertNotIn("BW_CLIENTID", setup_template)
        self.assertNotIn("BW_CLIENTSECRET", setup_template)

        profile_hook = (
            REPO_ROOT / "playbooks/roles/config/lxc_workstation_baseline/templates/workstation-setup-profile.sh.j2"
        ).read_text(encoding="utf-8")
        self.assertIn("/nix/var/nix/profiles/default/bin", profile_hook)
        self.assertIn(".nix-profile/bin", profile_hook)
        self.assertIn("workstation_home }}/.local/bin", profile_hook)
        self.assertIn("export PATH=", profile_hook)
        self.assertNotIn("mise", profile_hook)
        self.assertIn("-t 0", profile_hook)
        self.assertIn("-t 1", profile_hook)
        self.assertIn("SSH_CONNECTION", profile_hook)
        self.assertIn("SSH_TTY", profile_hook)
        self.assertIn("WORKSTATION_SETUP_SKIP", profile_hook)
        self.assertIn("workstation_setup_marker_path", profile_hook)
        self.assertIn("workstation_setup_bin_path", profile_hook)
        self.assertIn("workstation-update is missing or not executable", profile_hook)
        self.assertNotIn("setsid", profile_hook)
        self.assertNotIn("chezmoi apply", profile_hook)
        persistent_home_tasks = load_yaml(
            REPO_ROOT / "playbooks/roles/config/lxc_workstation_baseline/tasks/persistent_home.yml"
        )
        firewall_tasks = load_yaml(
            REPO_ROOT / "playbooks/roles/config/lxc_workstation_baseline/tasks/origin_firewall.yml"
        )
        persistent_home_task_names = [t.get("name") for t in persistent_home_tasks]
        firewall_task_names = [t.get("name") for t in firewall_tasks]
        self.assertIn("Assert workstation origin firewall inputs are valid", firewall_task_names)
        self.assertIn("Resolve workstation origin firewall allowlist address", firewall_task_names)
        self.assertIn("Assert workstation origin firewall host resolution succeeded", firewall_task_names)
        self.assertIn("Install workstation origin firewall package", firewall_task_names)
        self.assertIn("Deploy workstation origin firewall rules", firewall_task_names)
        self.assertIn("Deploy workstation origin firewall service", firewall_task_names)
        self.assertIn("Enable workstation origin firewall service", firewall_task_names)
        self.assertIn("Flush workstation origin firewall handlers", firewall_task_names)
        self.assertIn("Stop legacy AoE LAN proxy firewall service", firewall_task_names)
        self.assertIn("Remove legacy AoE LAN proxy firewall table", firewall_task_names)
        self.assertIn("Remove legacy AoE LAN proxy firewall rules", firewall_task_names)
        replacement_activation_and_legacy_cleanup = [
            "Enable workstation origin firewall service",
            "Flush workstation origin firewall handlers",
            "Stop legacy AoE LAN proxy firewall service",
            "Remove legacy AoE LAN proxy firewall table",
            "Remove legacy AoE LAN proxy firewall rules",
        ]
        self.assertEqual(
            [firewall_task_names.index(name) for name in replacement_activation_and_legacy_cleanup],
            sorted(firewall_task_names.index(name) for name in replacement_activation_and_legacy_cleanup),
            msg="replacement firewall activation must complete before ordered legacy AoE cleanup",
        )
        self.assertIn("Validate workstation persistent home mounts", persistent_home_task_names)
        self.assertIn("Ensure workstation persistent home targets exist", persistent_home_task_names)
        self.assertIn("Inspect workstation persistent home paths", persistent_home_task_names)
        self.assertIn(
            "Fail when workstation persistent home path conflicts with managed bind mount",
            persistent_home_task_names,
        )
        self.assertIn(
            "Remove legacy workstation persistent home symlinks before bind-mount migration",
            persistent_home_task_names,
        )
        self.assertIn("Ensure workstation persistent home mount points exist", persistent_home_task_names)
        self.assertIn("Mount workstation persistent home bind mounts", persistent_home_task_names)
        for removed_fragment in ("workstation_github_", "gh auth status", "user/keys"):
            self.assertNotIn(removed_fragment, rendered_tasks)

        rendered_persistent_home_tasks = yaml.safe_dump(persistent_home_tasks, sort_keys=True)
        rendered_firewall_tasks = yaml.safe_dump(firewall_tasks, sort_keys=True)
        expected_fragments = (
            "workstation_persistent_home_enabled",
            "workstation_persistent_home_root",
            "workstation_persistent_home_links",
            "workstation_persistent_home_mount_state",
            "workstation_persistent_home_fstab_path",
            "type",
            "findmnt",
            "state: '{{ workstation_persistent_home_mount_state",
            "opts: bind",
            "fstype: none",
            "ansible.posix.mount",
            "mode: '{{ item.mode",
        )
        for expected_fragment in expected_fragments:
            self.assertIn(expected_fragment, rendered_persistent_home_tasks)

        for expected_fragment in (
            "getent",
            "ahostsv4",
            "workstation_origin_firewall_protected_ports",
            "workstation_origin_firewall_allowed_hosts",
            "workstation_origin_firewall_nft_path",
            "workstation_origin_firewall_service_path",
            "workstation-aoe-proxy-firewall.service",
            "daemon_reload: true",
            "state: absent",
            "delegate_to: localhost",
            "check_mode: false",
            "failed_when: false",
            "notify: Restart workstation origin firewall",
            "could not resolve any IPv4 address for",
            "exactly one allowed inventory",
            "workstation_origin_firewall_allowed_endpoint",
            "workstation_origin_firewall_allowed_host",
        ):
            self.assertIn(expected_fragment, rendered_firewall_tasks)

        for expected_fragment in ("- nft", "- -c", "- -f"):
            self.assertIn(expected_fragment, rendered_firewall_tasks)

        self.assertNotIn("/etc/nftables.conf", rendered_firewall_tasks)
        self.assertIn("state: started", rendered_firewall_tasks)
        self.assertNotIn("state: restarted", rendered_firewall_tasks)

        template_dir = REPO_ROOT / "playbooks/roles/config/lxc_workstation_baseline/templates"
        environment = Environment(
            loader=FileSystemLoader(template_dir),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        rendered_firewall = environment.get_template("workstation-origin-firewall.nft.j2").render(
            workstation_origin_firewall_protected_ports=[4001, 9119, 18789, 8788],
            workstation_origin_firewall_allowed_ipv4=["192.0.2.10", "192.0.2.11"],
        )
        self.assertIn("elements = { 4001, 9119, 18789, 8788 }", rendered_firewall)
        self.assertIn("elements = { 192.0.2.10, 192.0.2.11 }", rendered_firewall)
        self.assertIn('iifname "lo" tcp dport @protected_tcp_ports accept', rendered_firewall)
        self.assertIn('ip saddr @allowed_ipv4 tcp dport @protected_tcp_ports accept', rendered_firewall)
        self.assertIn('tcp dport @protected_tcp_ports drop', rendered_firewall)
        loopback_accept_position = rendered_firewall.index(
            'iifname "lo" tcp dport @protected_tcp_ports accept'
        )
        allowed_ipv4_accept_position = rendered_firewall.index(
            'ip saddr @allowed_ipv4 tcp dport @protected_tcp_ports accept'
        )
        protected_port_drop_position = rendered_firewall.index('tcp dport @protected_tcp_ports drop')
        self.assertLess(loopback_accept_position, allowed_ipv4_accept_position)
        self.assertLess(allowed_ipv4_accept_position, protected_port_drop_position)
        for unprotected_port in (80, 443, 22):
            self.assertNotIn(str(unprotected_port), rendered_firewall)

        rendered_firewall_service = environment.get_template(
            "workstation-origin-firewall.service.j2"
        ).render(workstation_origin_firewall_nft_path="/tmp/firewall/custom-origin.nft")
        self.assertIn(
            "ExecStart=/usr/sbin/nft -f /tmp/firewall/custom-origin.nft",
            rendered_firewall_service,
        )
        self.assertIn(
            "ExecStop=/usr/sbin/nft delete table inet workstation_origin",
            rendered_firewall_service,
        )
        self.assertIn("RemainAfterExit=yes", rendered_firewall_service)

        enabled_service_task = next(
            task
            for task in firewall_tasks
            if task.get("name") == "Enable workstation origin firewall service"
        )
        self.assertEqual(
            enabled_service_task["ansible.builtin.systemd"]["name"],
            "{{ workstation_origin_firewall_service_path | basename }}",
        )
        disabled_service_task = next(
            task
            for task in firewall_tasks
            if task.get("name") == "Stop workstation origin firewall service when disabled"
        )
        self.assertEqual(
            disabled_service_task["ansible.builtin.systemd"]["name"],
            "{{ workstation_origin_firewall_service_path | basename }}",
        )

        firewall_handlers = load_yaml(
            REPO_ROOT / "playbooks/roles/config/lxc_workstation_baseline/handlers/main.yml"
        )
        self.assertEqual(len(firewall_handlers), 1)
        self.assertEqual(firewall_handlers[0]["name"], "Restart workstation origin firewall")
        self.assertEqual(
            firewall_handlers[0]["ansible.builtin.systemd"]["name"],
            "{{ workstation_origin_firewall_service_path | basename }}",
        )
        self.assertEqual(
            firewall_handlers[0]["ansible.builtin.systemd"]["state"],
            "restarted",
        )

    def test_workstation_bootstrap_deploy_wrapper_removed(self) -> None:
        self.assertFalse((REPO_ROOT / "scripts/workstation-bootstrap-deploy.sh").exists())

    def test_persistent_home_mount_probe_runs_in_check_mode(self) -> None:
        """The mount probe must opt out of check mode, or check runs fail on correct hosts.

        ansible.builtin.command has no check-mode support, so without check_mode: false the
        probe is skipped and the conflict assert loses its mount evidence. An already-mounted
        path then matches none of the assert's accepted shapes, and every check run fails on
        paths that are already in their desired state. The probe is read-only, so running it
        during a check mutates nothing.
        """
        tasks = flatten_tasks(
            load_yaml(
                REPO_ROOT
                / "playbooks/roles/config/lxc_workstation_baseline/tasks/persistent_home.yml"
            )
        )
        probes = [
            task
            for task in tasks
            if "findmnt" in str(task.get("ansible.builtin.command", {}).get("cmd", ""))
        ]

        self.assertEqual(len(probes), 1, "expected exactly one findmnt mount probe")
        self.assertIs(probes[0].get("check_mode"), False)
        self.assertIs(probes[0].get("changed_when"), False)


if __name__ == "__main__":
    unittest.main()
