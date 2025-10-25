# Archive: Deprecated On-Host Ansible Implementation

**⚠️ DEPRECATED AND UNSUPPORTED**

This directory contains the original Ansible implementation that was designed to run directly on the Proxmox host using shell commands (`pct`, `pvesh`, etc.).

## Why Archived

As of the remote controller refactor, this repository has migrated to an API-driven approach that runs from a remote Ubuntu LTS controller. The new implementation:
- Uses the `community.proxmox` collection for API-based management
- Runs from a remote controller (Ubuntu LTS LXC or dev machine)
- Manages only LXC containers (no VMs)
- Uses Ansible Vault for secrets
- Does not require shell access to the Proxmox host

## Current Implementation

See the root directory for the current remote controller implementation:
- `playbooks/` - API-driven playbooks
- `inventory/hosts.yml` - Remote controller inventory
- `docs/remote-controller-setup.md` - Setup guide
- `docs/ansible-remote-controller-spec.md` - Technical specification

## Contents of This Archive

- `ansible/roles/` - Legacy roles using shell commands
- `ansible/playbooks/` - Legacy playbooks for on-host execution
- `ansible/inventory/` - Legacy inventory for local execution
- `ansible/*.md` - Legacy documentation

**Do not use these files.** They are preserved for historical reference only.
