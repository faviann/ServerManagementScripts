# ServerManagementScripts

Ansible automation for managing Proxmox LXC containers via API from a remote controller.

## Overview

This repository provides Ansible playbooks and configuration to manage LXC containers on Proxmox VE using the Proxmox API. All playbooks run from a **remote controller** (Ubuntu LTS unprivileged LXC or your development machine) - no shell access to the Proxmox host is required.

### Key Features

- ✅ **Remote API-driven**: Manage Proxmox via API from a controller
- ✅ **LXC-only**: Focused exclusively on LXC container management (no VMs)
- ✅ **Secure**: API token authentication stored in Ansible Vault
- ✅ **Static Inventory**: Version-controlled configuration
- ✅ **Idempotent**: Safe to run multiple times

### LXC-Only Scope

This repository manages **LXC containers only**. Virtual machines (VMs/KVM) are not supported.

## Prerequisites

- **Controller**: Debian LTS (recommended as an unprivileged LXC or your workstation)
- **Python**: 3.10+ with `python3-venv` and `pip` available
- **Ansible**: ansible-core 2.19 / Ansible 12 (installed inside a virtual environment)
- **Python packages**: Installed from `requirements/pip.txt`
- **Ansible collections**: Installed from `collections/requirements.yml`
- **Network**: Controller must reach the Proxmox API (HTTPS port 8006)
- **Proxmox**: API token with LXC management permissions

### Proxmox Environment Defaults

These defaults are configured for the target homelab:

- **API host**: `proxmox.internal.faviann.com`
- **Node name**: `proxmox`
- **Network bridge**: `vmbr1`
- **Default template**: `local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst`

Adjust or override them in `inventory/group_vars/all/proxmox.yml`, host variables, or playbook vars as needed for your environment.

## Quick Start

### 1. Prepare a Python virtual environment

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip
python3 -m venv ~/.ansible-venv
source ~/.ansible-venv/bin/activate
python -m pip install --upgrade pip
```

> Reactivate the environment for future sessions with: `source ~/.ansible-venv/bin/activate`

### 2. Install Python dependencies (inside the venv)

```bash
python -m pip install -r requirements/pip.txt
```

### 3. Install Ansible collections (inside the venv)

```bash
ansible-galaxy collection install -r collections/requirements.yml
```

### 4. Configure API credentials

Create your vault file and vault password file. The password file should **not** be committed to version control.

```bash
# Create and edit the vault file from the example
cp inventory/group_vars/all/vault.example.yml inventory/group_vars/all/vault.yml
$EDITOR inventory/group_vars/all/vault.yml  # Add your Proxmox API token secret
ansible-vault encrypt inventory/group_vars/all/vault.yml

# Create the vault password file
echo "your-strong-passphrase" > ~/.ansible/vault-pass.txt
chmod 600 ~/.ansible/vault-pass.txt
```

### 5. Run connectivity checks

Validate SSH reachability to managed hosts and Proxmox API access:

```bash
ansible-playbook playbooks/lab-connectivity.yml
```

The output confirms which hosts respond to `ansible.builtin.ping` and the Proxmox API version detected.

### 6. Provision example LXC

Review and customize variables in `playbooks/provision_lxc_example.yml`, then provision:

```bash
ansible-playbook playbooks/provision_lxc_example.yml
```

## Repository Structure

```
.
├── collections/
│   └── requirements.yml          # Ansible collection dependencies
├── requirements/
│   └── pip.txt                   # Python package dependencies
├── inventory/
│   ├── hosts.yml                 # Static inventory file
│   ├── group_vars/               # Group-specific variables
│   │   └── all/
│   │       ├── proxmox.yml       # Non-secret Proxmox configuration
│   │       └── vault.yml         # Encrypted secrets
│   └── host_vars/                # Host-specific variables
│       └── jellyfin_lxc.yml      # Example host variables
├── playbooks/
│   ├── lab-connectivity.yml      # SSH + Proxmox API connectivity checks
│   ├── proxmox_api_check.yml     # API connectivity test
│   └── provision_lxc_example.yml # Example LXC provisioning
├── docs/
│   ├── remote-controller-setup.md        # Detailed setup guide
│   └── ansible-remote-controller-spec.md # Technical specification
└── archive/                      # Deprecated on-host implementation
```

## Documentation

- **[docs/remote-controller-setup.md](docs/remote-controller-setup.md)** - Complete setup and usage guide
- **[docs/ansible-remote-controller-spec.md](docs/ansible-remote-controller-spec.md)** - Architecture and technical specification

## Configuration

### Non-Secret Variables

Edit `inventory/group_vars/all/proxmox.yml` to configure your environment:

```yaml
proxmox_api_host: "proxmox.internal.faviann.com"           # Proxmox hostname or IP
proxmox_api_port: 8006                     # API port (default 8006)
proxmox_api_token_id: "ansible@pve!controller"  # API token ID
proxmox_default_node: "proxmox"           # Default node for operations
proxmox_verify_ssl: false                  # TLS verification (see below)
```

### Secret Variables (Ansible Vault)

Create `inventory/group_vars/all/vault.yml` from the example and encrypt:

```yaml
vault_proxmox_api_token_secret: "your-actual-token-secret"
```

```bash
ansible-vault encrypt inventory/group_vars/all/vault.yml
```

### Inventory

The `inventory/hosts.yml` defines two groups:

- **proxmox_api**: Controller host for API operations (runs locally)
- **lxcs**: Add your LXC containers here if you want to manage them via SSH after provisioning

## Creating API Tokens in Proxmox

1. Log into Proxmox web UI as `root@pam` or privileged user
2. Navigate to **Datacenter → Permissions → API Tokens**
3. Create a new token: `ansible@pve!controller`
4. Grant appropriate permissions (PVEVMAdmin role on relevant nodes/resources)
5. Copy the token secret immediately (shown only once)
6. Add the secret to your `vault.yml` file

## Example Playbooks

#### Connectivity validation

```bash
ansible-playbook playbooks/lab-connectivity.yml
```

Runs SSH ping checks against managed hosts and calls the Proxmox `/api2/json/version` endpoint using your API token.

### Check API Connectivity

```bash
ansible-playbook playbooks/proxmox_api_check.yml
```

Lists all LXC containers on the default node.

### Provision LXC Container

```bash
ansible-playbook playbooks/provision_lxc_example.yml
```

Creates an LXC container with:
- VMID: 123
- Hostname: app-01
- Template: debian-13-standard_13.1-2_amd64.tar.zst
- Storage: local-zfs
- Network: vmbr1 with DHCP
- Features: nesting enabled, unprivileged

Customize variables in the playbook as needed.

## TLS Certificate Verification

**Current status**: TLS verification is **disabled** by default (`proxmox_verify_ssl: false`) to support self-signed certificates commonly used in homelabs.

**TODO/Future hardening**:
1. Install a trusted certificate on Proxmox or distribute your CA bundle to the controller
2. Set `proxmox_verify_ssl: true` in `inventory/group_vars/all/proxmox.yml`
3. Configure CA path if needed via `api_ca_path` parameter

## Troubleshooting

### Cannot reach Proxmox API

- Verify controller can reach Proxmox host: `curl -k https://proxmox.internal.faviann.com:8006`
- Check firewall rules allow HTTPS (port 8006)
- Verify VPN/network connectivity

### Authentication fails

- Verify API token secret in vault: `ansible-vault view inventory/group_vars/all/vault.yml`
- Check token permissions in Proxmox web UI
- Ensure token ID format: `user@realm!tokenid` (e.g., `ansible@pve!controller`)

### Module not found

- Verify collections installed: `ansible-galaxy collection list | grep proxmox`
- Reinstall if needed: `ansible-galaxy collection install -r collections/requirements.yml --force`

### Python import errors

- Verify Python packages: `python3 -m pip list | grep -E "proxmoxer|requests"`
- Reinstall if needed: `python3 -m pip install --user -r requirements/pip.txt --force-reinstall`

## Migration from Legacy Implementation

The previous implementation (running Ansible on the Proxmox host with shell commands) has been archived to `archive/ansible/`. It is **deprecated and unsupported**.

The new API-driven approach provides:
- No shell access required to Proxmox host
- Better security (API tokens vs root access)
- Remote execution from any controller
- Consistent with Proxmox best practices

## Contributing

When adding new playbooks or roles:
- Use `community.proxmox` modules only (no shell commands)
- Target the `proxmox_api` inventory group
- Set `connection: local` and `gather_facts: false`
- Pass authentication via the `_proxmox_auth` variable pattern
- Document in playbook comments

## Support

- Issues: https://github.com/faviann/ServerManagementScripts/issues
- Documentation: See [docs/](docs/) directory

## License

Part of ServerManagementScripts repository.
