# Inventory Structure

This inventory drives LXC automation on Proxmox. Hosts inherit variables from
resource tiers (baseline CPU/RAM/disk) and optional capabilities (Docker,
WireGuard, GPU).

## Resource Tiers

| Group | CPU | RAM | Disk | Use Cases |
|-------|-----|-----|------|-----------|
| `tier_tiny` | 1 core | 1 GB | 8 GB | Monitoring agents, lightweight proxies |
| `tier_small` | 2 cores | 4 GB | 8 GB | Lightweight services |
| `tier_medium` | 4 cores | 16 GB | 8 GB | Application servers and media tooling |
| `tier_large` | 8 cores | 32 GB | 8 GB | Heavy workloads and download stacks |

## Capability Groups

| Group | Purpose | Key Variables |
|-------|---------|---------------|
| `cap_docker` | Docker runtime, compose, docker-agents baseline, and Hawser Standard remote access | `install_docker`, `proxmox_lxc_capability_defaults.features`, `docker_user`, `docker_agents_enabled`, `traefik_kop_enabled`, `dockhand_hawser_token` |
| `cap_gpu` | GPU passthrough for hardware acceleration | `enable_gpu_passthrough`, `configure_nvidia_runtime` |
| `cap_wireguard` | WireGuard kernel support | `enable_wireguard`, `lxc_wireguard_features` |

`cap_docker` baseline:
- `docker_agents_enabled: true`
- `traefik_kop_enabled: true`
- Hawser Standard runs by default on every non-`portal` Docker host

`portal` is the exception. It sets `portal_instance: true`, disables `traefik_kop_enabled`,
and does not run Hawser because it hosts Dockhand and Traefik instead of acting as a remote service host.

## Current Hosts

| Host | Tier | Capability Groups | Notes |
|------|------|-------------------|-------|
| `auth` | `tier_small` | `cap_docker` | Auth stack host |
| `portal` | `tier_medium` | `cap_docker` | Traefik host (`traefik_kop_enabled: false`) |
| `servarr` | `tier_medium` | `cap_docker` | Servarr application host |
| `seedbox` | `tier_large` | `cap_docker`, `cap_wireguard` | Download/tunneled host |

## Directory Layout

```text
inventory/
|-- hosts.yml
|-- group_vars/
|   |-- all/
|   |   |-- proxmox.yml
|   |   |-- vault.yml
|   |   `-- vault.yml.example
|   |-- proxmox_api/vars.yml
|   |-- tier_tiny/vars.yml
|   |-- tier_small/vars.yml
|   |-- tier_medium/vars.yml
|   |-- tier_large/vars.yml
|   |-- cap_docker/vars.yml
|   |-- cap_gpu/vars.yml
|   `-- cap_wireguard/vars.yml
`-- host_vars/
    |-- auth.yml
    |-- portal.yml
    |-- seedbox.yml
    `-- servarr.yml
```

## Variable Inheritance Example

For `servarr` (`tier_medium` + `cap_docker`), variables resolve in this order:

1. `group_vars/all/*.yml`
2. `group_vars/tier_medium/vars.yml`
3. `group_vars/cap_docker/vars.yml`
4. `host_vars/servarr.yml`

Tier and capability inputs:

```yaml
# group_vars/tier_medium/vars.yml
proxmox_lxc_group_defaults:
  cores: 4
  memory: 16384
  disk: "8"
  netif:
    net0: "name=eth0,bridge=vmbr1,firewall=0,ip=dhcp,ip6=auto,type=veth"

# group_vars/cap_docker/vars.yml
install_docker: true
docker_agents_enabled: true
traefik_kop_enabled: true
```

Host-specific overrides:

```yaml
# host_vars/servarr.yml
proxmox_lxc_overrides:
  vmid: 303
  hostname: servarr
```

## Adding a New Host

1. Pick exactly one resource tier.
2. Add capability groups as needed.
3. Add host to matching groups in `hosts.yml`.
4. Create `host_vars/<hostname>.yml`.

Example:

```yaml
# hosts.yml
tier_small:
  hosts:
    mynewhost:

cap_docker:
  hosts:
    mynewhost:
```

```yaml
# host_vars/mynewhost.yml
---
proxmox_lxc_overrides:
  vmid: 305
  hostname: mynewhost
  description: "My new service managed via Ansible"
  tags:
    - ansible
    - mynewhost
```

## Useful Commands

```bash
# Visualize group membership
./inspect.sh vars --graph

# Show merged vars for one host
./inspect.sh vars servarr
```

Hosts resolve via DNS as `{hostname}.faviann.vms`.
