# Ansible LXC Container Management - Usage Guide

This guide explains how to use the Ansible automation to create and manage Proxmox LXC containers running Docker and Dockge.

## Table of Contents
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Creating a New Container](#creating-a-new-container)
- [Updating Existing Containers](#updating-existing-containers)
- [Customizing Container Configuration](#customizing-container-configuration)
- [Common Scenarios](#common-scenarios)
- [Troubleshooting](#troubleshooting)

## Prerequisites

### Required Software
- **Proxmox VE** 7.x or 8.x
- **Ansible** 2.10 or higher
- **Python** 3.8 or higher

### Required Ansible Collections
```bash
ansible-galaxy collection install community.general
```

### Host Requirements
- Access to Proxmox host with root privileges
- ZFS volume mounted at `/rpool/data/subvol-200-disk-1`
- Dockge template available at `/rpool/data/subvol-200-disk-1/.template`
- Network bridge `vmbr1` configured
- Host directories: `/conf`, `/ephemeral`, `/tank`

### Optional Requirements
- **For GPU support**: NVIDIA drivers and nvidia-modprobe installed on Proxmox host
- **For Wireguard**: Kernel support for tun/tap devices

## Installation

1. **Clone the repository**:
   ```bash
   cd /root
   git clone https://github.com/faviann/ServerManagementScripts.git
   cd ServerManagementScripts/ansible
   ```

2. **Install Ansible collections**:
   ```bash
   ansible-galaxy collection install community.general
   ```

3. **Verify installation**:
   ```bash
   ansible-playbook --version
   ```

4. **Test connectivity**:
   ```bash
   ansible -m ping localhost
   ```

## Quick Start

### Create a basic container without GPU:
```bash
cd /root/ServerManagementScripts/ansible
ansible-playbook playbooks/create-lxc.yml -e "target_host=example-app"
```

### Create a container with GPU support:
```bash
ansible-playbook playbooks/create-lxc.yml -e "target_host=gpu-app"
```

### Update all containers:
```bash
ansible-playbook playbooks/update-all.yml
```

## Creating a New Container

### Step 1: Create Configuration File

Create a new file in `host_vars/` with your container name:

```bash
nano host_vars/my-container.yml
```

Add the following configuration:

```yaml
---
# Container identification
lxc_id: 203
container_name: "my-container"

# Resource allocation
disk_size: 8          # GB
ram_size: 8192        # MB
swap_size: 8192       # MB
core_count: 4

# Features
enable_nvidia: false
enable_wireguard: false
```

### Step 2: Add to Inventory

Edit `inventory/hosts.yml` and add your container:

```yaml
    lxc_containers:
      hosts:
        # ... existing containers ...
        my-container:
          lxc_id: 203
```

### Step 3: Run the Playbook

```bash
ansible-playbook playbooks/create-lxc.yml -e "target_host=my-container"
```

### Step 4: Access Your Container

After creation (wait ~1 minute for reboot):
- **Dockge UI**: http://my-container.faviann.vms
- **SSH/Console**: `pct enter 203`

## Updating Existing Containers

### Update a Single Container

To reconfigure an existing container:

```bash
ansible-playbook playbooks/configure-lxc.yml -e "target_host=my-container"
```

This is idempotent and safe to run multiple times.

### Update All Containers

To update system packages on all containers:

```bash
ansible-playbook playbooks/update-all.yml
```

This will:
- Update apt package cache
- Upgrade all packages
- Restart Dockge if needed
- Process containers one at a time

### Selective Updates with Tags

Update only specific parts:

```bash
# Only update NVIDIA configuration
ansible-playbook playbooks/configure-lxc.yml -e "target_host=gpu-app" --tags nvidia

# Only update Dockge
ansible-playbook playbooks/configure-lxc.yml -e "target_host=my-container" --tags dockge

# Only configure UID/GID mappings
ansible-playbook playbooks/configure-lxc.yml -e "target_host=my-container" --tags configure
```

## Customizing Container Configuration

### Resource Allocation

Adjust in `host_vars/{container_name}.yml`:

```yaml
disk_size: 16         # Increase disk to 16GB
ram_size: 16384       # Increase RAM to 16GB
swap_size: 16384      # Increase swap to 16GB
core_count: 8         # Allocate 8 CPU cores
```

### Enable GPU Support

```yaml
enable_nvidia: true
```

**Important**: After first GPU setup, you must manually edit:
```bash
pct enter {lxc_id}
nano /etc/nvidia-container-runtime/config.toml
# Change: no-cgroups = false
# To:     no-cgroups = true
systemctl restart docker
```

### Enable Wireguard/VPN

```yaml
enable_wireguard: true
```

### Custom Network Bridge

Override the default bridge in `host_vars`:

```yaml
network_bridge: vmbr0  # Use different bridge
```

### Custom Mount Points

Override mount point paths in `host_vars`:

```yaml
mp3_host_path: "/mnt/storage"  # Custom data path
mp3_mountpoint: "/storage"     # Custom mount point inside container
```

### Journald Log Limits

Adjust in `group_vars/all.yml` or `host_vars/{container_name}.yml`:

```yaml
journald_max_use: "2000M"      # Allow 2GB of logs
journald_max_file_size: "50M"  # 50MB per file
```

## Common Scenarios

### Scenario 1: Basic Web Application Container

**File**: `host_vars/webapp.yml`
```yaml
---
lxc_id: 210
container_name: "webapp"
disk_size: 8
ram_size: 4096
core_count: 2
enable_nvidia: false
enable_wireguard: false
```

**Create**:
```bash
ansible-playbook playbooks/create-lxc.yml -e "target_host=webapp"
```

### Scenario 2: Machine Learning Container with GPU

**File**: `host_vars/ml-server.yml`
```yaml
---
lxc_id: 220
container_name: "ml-server"
disk_size: 32
ram_size: 32768
core_count: 16
enable_nvidia: true
enable_wireguard: false
```

**Create**:
```bash
ansible-playbook playbooks/create-lxc.yml -e "target_host=ml-server"
# Then manually configure NVIDIA cgroups as shown above
```

### Scenario 3: VPN Gateway Container

**File**: `host_vars/vpn-gateway.yml`
```yaml
---
lxc_id: 230
container_name: "vpn-gateway"
disk_size: 8
ram_size: 2048
core_count: 2
enable_nvidia: false
enable_wireguard: true
```

**Create**:
```bash
ansible-playbook playbooks/create-lxc.yml -e "target_host=vpn-gateway"
```

### Scenario 4: Media Server with GPU Transcoding

**File**: `host_vars/plex.yml`
```yaml
---
lxc_id: 240
container_name: "plex"
disk_size: 16
ram_size: 16384
core_count: 8
enable_nvidia: true
enable_wireguard: false
```

**Create**:
```bash
ansible-playbook playbooks/create-lxc.yml -e "target_host=plex"
```

### Scenario 5: Create Multiple Containers

Create multiple container configs, add them to inventory, then:

```bash
# Create them one by one
for container in webapp db-server redis; do
  ansible-playbook playbooks/create-lxc.yml -e "target_host=$container"
done
```

## Troubleshooting

### Container Creation Fails

**Problem**: Container creation fails with "template not found"

**Solution**: 
```bash
# Manually download template
pveam update
pveam download local debian-12-standard_amd64.tar.zst
```

---

**Problem**: "ZFS volume not mounted"

**Solution**:
```bash
# Check if ZFS volume is mounted
mount | grep subvol-200-disk-1

# If not mounted, mount it
zfs mount rpool/data/subvol-200-disk-1
```

---

**Problem**: ".template folder does not exist"

**Solution**: Create the template folder at `/rpool/data/subvol-200-disk-1/.template` with Dockge configuration.

### Container Configuration Issues

**Problem**: UID/GID mappings not working

**Solution**: 
```bash
# Stop and start container (not restart)
pct stop {lxc_id}
pct start {lxc_id}
```

---

**Problem**: Mount points not accessible

**Solution**:
```bash
# Check mount points on host
ls -la /conf /ephemeral /tank

# Check permissions
chmod 755 /conf /ephemeral /tank
```

### NVIDIA GPU Issues

**Problem**: GPU not accessible in container

**Solution**:
```bash
# On Proxmox host
nvidia-smi  # Verify GPU is visible

# Check nvidia-modprobe
which nvidia-modprobe

# Verify hook script exists
ls -la /usr/share/lxc/hooks/nvidia
```

---

**Problem**: Docker can't access GPU

**Solution**:
```bash
pct enter {lxc_id}
# Edit config.toml
nano /etc/nvidia-container-runtime/config.toml
# Change no-cgroups = false to true
systemctl restart docker

# Test
docker run --rm --gpus all nvidia/cuda:11.0-base nvidia-smi
```

### Dockge Issues

**Problem**: Dockge not starting

**Solution**:
```bash
pct enter {lxc_id}
cd /conf/docker/dockge
sudo -u dockeruser docker compose ps
sudo -u dockeruser docker compose logs

# Restart
sudo -u dockeruser docker compose restart
```

---

**Problem**: Can't access Dockge web interface

**Solution**:
```bash
# Check if Dockge is listening
pct exec {lxc_id} -- bash -c "netstat -tlnp | grep 5001"

# Check DNS resolution
ping my-container.faviann.vms

# Check from container
pct enter {lxc_id}
curl localhost:5001
```

### Network Issues

**Problem**: Container has no network connectivity

**Solution**:
```bash
# Check bridge exists
brctl show vmbr1

# Inside container, check network
pct enter {lxc_id}
ip addr
ping 8.8.8.8
```

---

**Problem**: DHCP not working

**Solution**:
```bash
# Restart networking in container
pct exec {lxc_id} -- bash -c "systemctl restart networking"

# Or manually configure
pct set {lxc_id} --net0 name=eth0,bridge=vmbr1,ip=192.168.1.100/24,gw=192.168.1.1,type=veth
```

### Ansible Issues

**Problem**: "Module not found" errors

**Solution**:
```bash
ansible-galaxy collection install community.general
```

---

**Problem**: Playbook hangs or times out

**Solution**:
```bash
# Increase timeout in ansible.cfg
timeout = 60

# Run with verbose output
ansible-playbook playbooks/create-lxc.yml -e "target_host=my-container" -vvv
```

---

**Problem**: Permission denied

**Solution**:
```bash
# Ensure running as root or with sudo
sudo ansible-playbook playbooks/create-lxc.yml -e "target_host=my-container"
```

### Idempotency Issues

**Problem**: Playbook fails on second run

**Solution**: This should not happen! Our playbooks are designed to be idempotent. If this occurs:
1. Check the specific task that's failing
2. Look for error messages about "already exists"
3. Review the task's `when` conditions

Report issues to the repository maintainer.

### General Debugging

Enable verbose output:
```bash
# Level 1: Basic
ansible-playbook playbooks/create-lxc.yml -e "target_host=my-container" -v

# Level 2: More detail
ansible-playbook playbooks/create-lxc.yml -e "target_host=my-container" -vv

# Level 3: Full debug
ansible-playbook playbooks/create-lxc.yml -e "target_host=my-container" -vvv
```

Check Ansible logs:
```bash
tail -f ansible.log
```

Check container logs:
```bash
pct exec {lxc_id} -- bash -c "journalctl -xe"
```

## Best Practices

1. **Always test in a development environment first**
2. **Back up important containers before making changes**
3. **Use version control for your host_vars configurations**
4. **Document any custom configurations**
5. **Keep container IDs organized** (e.g., 200-299 for production, 300-399 for dev)
6. **Regular updates**: Run `update-all.yml` monthly
7. **Monitor logs**: Check `/var/log/pve/tasks/` for Proxmox task logs
8. **Resource planning**: Don't over-allocate RAM/CPU across all containers

## Getting Help

- Check logs: `ansible.log` and `/var/log/pve/tasks/`
- Review Proxmox web interface for container status
- Check container console: `pct enter {lxc_id}`
- Read `README-HOW-IT-WORKS.md` for technical details
- Open an issue on GitHub: https://github.com/faviann/ServerManagementScripts/issues

## Next Steps

- Read [README-HOW-IT-WORKS.md](README-HOW-IT-WORKS.md) to understand the architecture
- Customize `group_vars/all.yml` for your environment
- Create host_vars for your specific containers
- Set up monitoring and alerting for your containers
- Implement backup strategies for important containers
