# Inventory Structure

This directory contains the Ansible inventory for managing Proxmox LXC containers using a resource-based grouping strategy.

## Quick Reference

### Resource Tiers

| Group | CPU | RAM | Disk | Use Cases |
|-------|-----|-----|------|-----------|
| `tiny_servers` | 1 core | 512 MB | 8 GB | Monitoring agents, lightweight proxies |
| `small_servers` | 2 cores | 2 GB | 8 GB | Development tools, small web services |
| `medium_servers` | 4 cores | 8 GB | 8 GB | Application servers, media processing |
| `large_servers` | 8 cores | 16 GB | 8 GB | Database servers, media servers with transcoding |

### Functional Groups

| Group | Purpose | Key Variables |
|-------|---------|---------------|
| `docker_hosts` | Docker runtime and compose | `install_docker`, `lxc_features` |
| `gpu_access` | GPU passthrough for hardware acceleration | `enable_gpu_passthrough`, `configure_nvidia_runtime` |
| `wireguard_hosts` | WireGuard VPN kernel module access | `enable_wireguard`, `wireguard_kernel_module_access` |
| `service_agents` | Service management tools (subset of docker_hosts) | `configure_traefik_kop`, `configure_traefik_socket_proxy`, `configure_dockwatch` |

## Current Hosts

```
codeserver:
  Resource Tier: small_servers (2 cores, 2GB RAM)
  Functional Groups: docker_hosts, service_agents
  VMID: 301

frontend:
  Resource Tier: small_servers (2 cores, 2GB RAM)
  Functional Groups: docker_hosts, service_agents
  VMID: 302

media:
  Resource Tier: medium_servers (4 cores, 8GB RAM)
  Functional Groups: docker_hosts, gpu_access, service_agents
  VMID: 303

jellyfin:
  Resource Tier: large_servers (8 cores, 16GB RAM)
  Functional Groups: docker_hosts, gpu_access
  VMID: 304
  Note: NOT a service_agent (doesn't need traefik tooling)
  Override: 32GB RAM instead of 16GB default
```

## Directory Structure

```
inventory/
├── hosts.yml                       # Main inventory file with host groupings
│
├── group_vars/                     # Group-level variables
│   ├── all/                       # Variables for all hosts
│   │   ├── proxmox.yml           # Proxmox API and infrastructure config
│   │   └── vault.yml             # Encrypted secrets (API tokens)
│   │
│   ├── proxmox_api.yml           # API controller configuration
│   │
│   ├── tiny_servers.yml          # Resource tier: 1 core, 512MB RAM
│   ├── small_servers.yml         # Resource tier: 2 cores, 2GB RAM
│   ├── medium_servers.yml        # Resource tier: 4 cores, 8GB RAM
│   ├── large_servers.yml         # Resource tier: 8 cores, 16GB RAM
│   │
│   ├── docker_hosts.yml          # Docker installation and configuration
│   ├── gpu_access.yml            # GPU passthrough capabilities
│   ├── wireguard_hosts.yml       # WireGuard VPN configuration
│   └── service_agents.yml        # Service management tools
│
└── host_vars/                     # Host-specific variables
    ├── codeserver.yml            # VSCode server (small, docker, service_agent)
    ├── frontend.yml              # Frontend service (small, docker, service_agent)
    ├── media.yml                 # Media processing (medium, docker, gpu, service_agent)
    └── jellyfin.yml              # Media server (large, docker, gpu) with overrides
```

## Variable Inheritance Example

For the `media` host, variables are inherited in this order:

1. **All Hosts** (`group_vars/all/proxmox.yml`)
   - Proxmox API configuration
   - Default mounts and ID mappings

2. **Resource Tier** (`group_vars/medium_servers.yml`)
   ```yaml
   lxc_cores: 4
   lxc_memory: 8192
   lxc_disk: "8"
   lxc_network_bridge: vmbr1
   ```

3. **Functional Groups** (merged from multiple files)
   - From `docker_hosts.yml`:
     ```yaml
     install_docker: true
     lxc_features: [nesting=1, keyctl=1]
     docker_user: dockeruser
     ```
   - From `gpu_access.yml`:
     ```yaml
     enable_gpu_passthrough: true
     configure_nvidia_runtime: true
     ```
   - From `service_agents.yml`:
     ```yaml
     configure_traefik_kop: true
     configure_traefik_socket_proxy: true
     configure_dockwatch: true
     ```

4. **Host-Specific** (`host_vars/media.yml`)
   ```yaml
   proxmox_lxc:
     vmid: 303
     hostname: media
     cores: "{{ lxc_cores }}"      # Uses inherited value: 4
     memory: "{{ lxc_memory }}"    # Uses inherited value: 8192
   ```

## Adding a New Host

1. **Choose resource tier** based on workload needs
2. **Select functional groups** based on capabilities needed
3. **Add to `hosts.yml`** under appropriate groups
4. **Create `host_vars/{hostname}.yml`** with specific configuration

Example for adding a new `database` host:

```yaml
# In hosts.yml
large_servers:
  hosts:
    database:
      proxmox_lxc:
        vmid: 305
        hostname: database
        description: "PostgreSQL database server"

docker_hosts:
  hosts:
    database:

lxcs:
  hosts:
    database:
```

```yaml
# In host_vars/database.yml
---
proxmox_lxc:
  vmid: 305
  hostname: database
  description: "PostgreSQL database server"
  node: "{{ proxmox_default_node }}"
  cores: "{{ lxc_cores }}"
  memory: "{{ lxc_memory }}"
  # ... rest of configuration
```

## Testing Commands

```bash
# View inventory structure
ansible-inventory --graph

# Check variables for a specific host
ansible-inventory --host media --yaml

# View all hosts in a group
ansible-inventory --graph small_servers

# Validate YAML syntax
yamllint inventory/
```

## Documentation

For detailed information, see [docs/inventory-structure-guide.md](../docs/inventory-structure-guide.md)

## Network Resolution

Hosts will be resolved via DNS as `{hostname}.faviann.vms` or through the Proxmox API using their container ID or name.
