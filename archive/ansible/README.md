# Ansible LXC Container Management

Production-ready Ansible automation for creating and managing Proxmox LXC containers running Docker and Dockge.

## Overview

This Ansible solution converts the manual bash script workflow into a fully automated, idempotent, and version-controlled system for managing 5-20 LXC containers on Proxmox.

## Features

- ✅ **Fully Idempotent**: Safe to run multiple times without side effects
- ✅ **Version Controlled**: All configuration in YAML files
- ✅ **Modular Design**: Separate roles for different concerns
- ✅ **NVIDIA GPU Support**: Optional GPU passthrough configuration
- ✅ **Wireguard/VPN Support**: Optional tunnel device configuration
- ✅ **Docker & Dockge**: Automated setup with proper user permissions
- ✅ **UID/GID Mapping**: Correct file permissions for bind mounts
- ✅ **Automated Updates**: Update all containers with a single command

## Quick Start

```bash
# Navigate to ansible directory
cd /root/ServerManagementScripts/ansible

# Create a new container
ansible-playbook playbooks/create-lxc.yml -e "target_host=example-app"

# Create a container with GPU
ansible-playbook playbooks/create-lxc.yml -e "target_host=gpu-app"

# Update all containers
ansible-playbook playbooks/update-all.yml
```

## Documentation

- **[README-USAGE.md](README-USAGE.md)**: Complete user guide with examples, troubleshooting, and best practices
- **[README-HOW-IT-WORKS.md](README-HOW-IT-WORKS.md)**: Technical deep dive, architecture, and extension guide

## What This Automates

Based on the original `setupDockerLXC.sh` bash script, this automation handles:

### Container Creation
- Download Debian 12 standard template (if not cached)
- Create LXC container with specified ID, disk, RAM, CPU, hostname
- Configure networking on vmbr1 bridge with DHCP
- Set up 4 mount points:
  - `mp0`: ZFS shared volume (256G)
  - `mp1`: `/conf` (read-only)
  - `mp2`: `/ephemeral`
  - `mp3`: `/tank` (data storage)

### UID/GID Mapping
- Configure proper UID/GID mappings for file permissions
- Map container UID 1000 to host UID 1001
- Ensure bind mounts work correctly

### Optional Features
- **NVIDIA GPU Passthrough**: Adds LXC configuration for GPU access
- **Wireguard/VPN**: Adds /dev/net/tun mount for tunneling

### Inside Container Setup
- Update system packages
- Copy Dockge template from `.template` folder
- Create bind mount: `/shared/{container_name}` → `/conf/docker`
- Create `dockeruser` (UID 1000, no password, docker + sudo groups)
- Start Dockge: `docker compose up -d`
- Configure journald log limits (1000M max, 20M per file)
- Install NVIDIA Container Toolkit (if GPU enabled)
- Reboot container

## Directory Structure

```
ansible/
├── ansible.cfg              # Ansible configuration
├── inventory/
│   └── hosts.yml            # Container inventory
├── host_vars/               # Per-container configs
│   ├── example-app.yml      # Example without GPU
│   └── gpu-app.yml          # Example with GPU
├── group_vars/
│   └── all.yml              # Default values
├── roles/
│   ├── lxc_create/          # Container creation
│   ├── lxc_configure/       # UID/GID mapping
│   ├── lxc_nvidia/          # GPU passthrough
│   ├── lxc_wireguard/       # VPN support
│   └── lxc_dockge/          # Docker & Dockge setup
└── playbooks/
    ├── create-lxc.yml       # Create new container
    ├── configure-lxc.yml    # Reconfigure existing
    └── update-all.yml       # Update all containers
```

## Prerequisites

- Proxmox VE 7.x or 8.x
- Ansible 2.10 or higher
- Python 3.8 or higher
- ZFS volume at `/rpool/data/subvol-200-disk-1`
- Dockge template at `/rpool/data/subvol-200-disk-1/.template`

## Creating Your First Container

1. **Create configuration**:
```bash
cat > host_vars/my-app.yml <<EOF
---
lxc_id: 203
container_name: "my-app"
disk_size: 8
ram_size: 8192
core_count: 4
enable_nvidia: false
enable_wireguard: false
EOF
```

2. **Add to inventory**:
```yaml
# Edit inventory/hosts.yml
    lxc_containers:
      hosts:
        my-app:
          lxc_id: 203
```

3. **Run playbook**:
```bash
ansible-playbook playbooks/create-lxc.yml -e "target_host=my-app"
```

4. **Access Dockge**:
```
http://my-app.faviann.vms
```

## Configuration Examples

### Basic Container (No GPU)
```yaml
lxc_id: 201
container_name: "webapp"
disk_size: 8
ram_size: 4096
core_count: 2
enable_nvidia: false
enable_wireguard: false
```

### GPU-Enabled Container
```yaml
lxc_id: 202
container_name: "ml-server"
disk_size: 32
ram_size: 32768
core_count: 16
enable_nvidia: true
enable_wireguard: false
```

### VPN Gateway Container
```yaml
lxc_id: 203
container_name: "vpn-gateway"
disk_size: 8
ram_size: 2048
core_count: 2
enable_nvidia: false
enable_wireguard: true
```

## Common Commands

```bash
# Create new container
ansible-playbook playbooks/create-lxc.yml -e "target_host=CONTAINER_NAME"

# Reconfigure existing container (idempotent)
ansible-playbook playbooks/configure-lxc.yml -e "target_host=CONTAINER_NAME"

# Update all containers
ansible-playbook playbooks/update-all.yml

# Run with verbose output
ansible-playbook playbooks/create-lxc.yml -e "target_host=CONTAINER_NAME" -vvv

# Run only specific role
ansible-playbook playbooks/configure-lxc.yml -e "target_host=CONTAINER_NAME" --tags nvidia

# Dry run (check mode)
ansible-playbook playbooks/create-lxc.yml -e "target_host=CONTAINER_NAME" --check
```

## Key Design Decisions

1. **Idempotency First**: Every task checks before executing
2. **Ansible Modules**: Use native modules over shell commands when possible
3. **Blockinfile for Multi-line**: UID mappings and NVIDIA config use blockinfile with markers
4. **Role Separation**: Each concern (creation, GPU, VPN, etc.) is a separate role
5. **Local Execution**: Runs on Proxmox host itself (no SSH overhead)
6. **Conditional Roles**: GPU and Wireguard roles only run when enabled
7. **Configuration as Code**: All settings in version-controlled YAML files

## Troubleshooting

See [README-USAGE.md](README-USAGE.md#troubleshooting) for comprehensive troubleshooting guide.

Quick checks:
```bash
# Check container status
pct status {lxc_id}

# Check Ansible logs
tail -f ansible.log

# Test playbook syntax
ansible-playbook playbooks/create-lxc.yml --syntax-check

# List all tasks
ansible-playbook playbooks/create-lxc.yml --list-tasks
```

## Support

- Issues: https://github.com/faviann/ServerManagementScripts/issues
- Documentation: See [README-USAGE.md](README-USAGE.md) and [README-HOW-IT-WORKS.md](README-HOW-IT-WORKS.md)

## License

Part of ServerManagementScripts repository.

## Original Bash Script

This automation is based on `setupDockerLXC.sh` which manually created containers. This Ansible solution provides:
- Idempotency (can run multiple times)
- Version control (Git-friendly YAML)
- Modularity (separate roles)
- Scalability (manage multiple containers)
- No external dependencies (no Proxmox VE Helper Scripts)

---

**Get Started**: Read [README-USAGE.md](README-USAGE.md) for step-by-step instructions!
