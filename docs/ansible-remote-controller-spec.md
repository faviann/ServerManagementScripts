# Ansible Remote Controller Refactor Spec (LXC-only)

## Goal
Run all Ansible playbooks from a remote controller (Ubuntu LTS unprivileged LXC or the developer's machine) instead of on the Proxmox host. Manage ONLY LXC containers via the Proxmox API using the community.proxmox collection and API tokens. Use static inventory files. Store secrets in Ansible Vault.

## Scope
- In-scope
  - Remote execution from controller (LXC or dev machine) against Proxmox API.
  - API token-based auth configuration (in Vault).
  - Static inventory structure that works identically from LXC or dev machine.
  - Replace any shell/pvesh/qm/pct assumptions with API-driven LXC modules.
  - Example playbooks:
    - API connectivity check (LXC info).
    - Idempotent LXC provisioning skeleton.
  - Docs: controller setup, Vault usage, run commands, TLS TODOs.
- Out of scope
  - VMs are NOT managed (no kvm).
  - Backward compatibility with running on the Proxmox host.
  - TLS/cert hardening (documented as TODO).
  - Automated tests (optional future addition).
  - Automatic creation/management of API tokens (documented as future enhancement).

## Environment and decisions
- Controller: Ubuntu LTS (unprivileged LXC preferred; dev machine supported).
- Network: Controller reaches Proxmox API directly (VPN in place).
- Ansible: ansible-core 2.19.x.
- Collections: community.proxmox (primary), community.general (if needed).
- Python: proxmoxer, requests (installed on controller).
- Inventory: Static YAML, maintained manually.
- Secrets: Ansible Vault (encrypted file; vault password not committed).
- TLS: validate_certs disabled for now (TODO to enable later).
- LXC specifics: prefer unprivileged LXCs; use nesting if needed (e.g., for Docker-in-LXC).

## Proxmox defaults (homelab)
- Node name: proxmox.vms
- Storage (CT disks): local-zfs
- Network bridge: vmbr1
- Template: local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst
  - Given filesystem path was /var/lib/vz/template/cache/debian-13-standard_13.1-2_amd64.tar.zst

## Repository changes (files to add)
- collections/requirements.yml (pin community.proxmox; add community.general if needed)
- requirements/pip.txt (proxmoxer, requests)
- inventory/hosts.yml (static inventory with proxmox_api and lxcs groups)
- group_vars/all/proxmox.yml (non-secret API vars)
- group_vars/all/vault.example.yml (unencrypted template; user will create and encrypt vault.yml locally)
- playbooks/proxmox_api_check.yml (LXC info from API)
- playbooks/provision_lxc_example.yml (idempotent LXC provisioning skeleton using provided defaults)
- docs/remote-controller-setup.md (setup and usage)
- .gitignore (ignore local vault password files)

## Variable schema
- proxmox_api_host: Proxmox hostname or IP (HTTPS: 8006)
- proxmox_api_token_id: Token ID (e.g., ansible@pve!controller)
- proxmox_api_token_secret: Stored in Vault (vault_proxmox_api_token_secret)
- proxmox_verify_ssl: false (TODO to enable later)
- proxmox_default_node: default node name for info queries (proxmox.vms)

## Playbook conventions
- API operations:
  - hosts: proxmox_api
  - connection: local
  - gather_facts: false
  - Use community.proxmox modules:
    - proxmox_lxc and proxmox_lxc_info for LXC lifecycle and discovery
  - Pass auth vars: api_host, api_token_id, api_token_secret, validate_certs

## Acceptance criteria
- From a clean Ubuntu LTS controller (LXC or dev machine), following docs:
  - Ansible 2.19 installed; collections and Python deps installed.
  - Vault secrets configured.
  - Running `ansible-playbook -i inventory/hosts.yml playbooks/proxmox_api_check.yml` succeeds and returns LXC list via API.
- No tasks assume shell access to the Proxmox host.
- No VM modules or VM groups present.
- Secrets are not committed in plaintext.
