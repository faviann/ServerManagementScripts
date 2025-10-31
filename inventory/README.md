# Inventory Structure



This inventory drives LXC automation on Proxmox. Hosts inherit variables from

resource **tiers** (baseline CPU/RAM/disk) and optional **capabilities** (extra

features such as Docker or GPU access).



## Resource Tiers



| Group | CPU | RAM | Disk | Use Cases |

|-------|-----|-----|------|-----------|

| `tier_tiny` | 1 core | 512 MB | 8 GB | Monitoring agents, lightweight proxies |

| `tier_small` | 2 cores | 2 GB | 8 GB | Development tools, small web services |

| `tier_medium` | 4 cores | 8 GB | 8 GB | Application servers, media processing |

| `tier_large` | 8 cores | 16 GB | 8 GB | Database services, media servers with transcoding |



## Capability Groups



| Group | Purpose | Key Variables |

|-------|---------|---------------|

| `cap_docker` | Docker runtime and compose | `install_docker`, `lxc_features`, `docker_user` |

| `cap_gpu` | GPU passthrough for hardware acceleration | `enable_gpu_passthrough`, `configure_nvidia_runtime` |

| `cap_wireguard` | WireGuard VPN kernel module access | `enable_wireguard`, `lxc_wireguard_features` |

| `cap_service_agents` | Service management tooling (subset of `cap_docker`) | `configure_traefik_kop`, `configure_traefik_socket_proxy`, `configure_dockwatch` |



## Current Hosts



```

codeserver:

  Resource Tier: tier_small (2 cores, 2 GB RAM)

  Capability Groups: cap_docker, cap_service_agents

  VMID: 301



frontend:

  Resource Tier: tier_small (2 cores, 2 GB RAM)

  Capability Groups: cap_docker, cap_service_agents

  VMID: 302



media:

  Resource Tier: tier_medium (4 cores, 8 GB RAM)

  Capability Groups: cap_docker, cap_gpu, cap_service_agents

  VMID: 303



jellyfin:

  Resource Tier: tier_large (8 cores, 16 GB RAM)

  Capability Groups: cap_docker, cap_gpu

  VMID: 304

  Override: 32 GB RAM instead of 16 GB default

```



## Directory Layout



```

inventory/

├── hosts.yml                       # Main inventory with host groupings

│

├── group_vars/                     # Group-level variables

│   ├── all/                        # Variables for all hosts

│   │   ├── proxmox.yml             # Proxmox API and infrastructure config

│   │   └── vault.yml               # Encrypted secrets (API tokens)

│   │

│   ├── proxmox_api/                # API controller configuration

│   │   └── vars.yml

│   │

│   ├── tier_tiny/                  # Resource tier: 1 core, 512 MB RAM

│   │   └── vars.yml

│   ├── tier_small/                 # Resource tier: 2 cores, 2 GB RAM

│   │   └── vars.yml

│   ├── tier_medium/                # Resource tier: 4 cores, 8 GB RAM

│   │   └── vars.yml

│   ├── tier_large/                 # Resource tier: 8 cores, 16 GB RAM

│   │   └── vars.yml

│   │

│   ├── cap_docker/                 # Docker installation and configuration

│   │   └── vars.yml

│   ├── cap_gpu/                    # GPU passthrough capabilities

│   │   └── vars.yml

│   ├── cap_wireguard/              # WireGuard VPN configuration

│   │   └── vars.yml

│   └── cap_service_agents/         # Service management tools

│       └── vars.yml

│

└── host_vars/                      # Host-specific variables

    ├── codeserver.yml              # VSCode server (tier_small + cap_docker/cap_service_agents)

    ├── frontend.yml                # Frontend service (tier_small + cap_docker/cap_service_agents)

    ├── media.yml                   # Media processing (tier_medium + cap_docker/cap_gpu)

    └── jellyfin.yml                # Media server (tier_large + cap_docker/cap_gpu)

```



## Variable Inheritance Example



For the `media` host, variables resolve in this order:



1. **All Hosts** (`group_vars/all/proxmox.yml`)

   - Proxmox API configuration

   - Default mounts and ID-mapping

2. **Resource Tier** (`group_vars/tier_medium/vars.yml`)

   ```yaml

   lxc_cores: 4

   lxc_memory: 8192

   lxc_disk: "8"

   lxc_network_bridge: vmbr1

   ```

3. **Capability Groups** (merged from multiple files)

   - From `group_vars/cap_docker/vars.yml`:

     ```yaml

     install_docker: true

     lxc_features:

       - nesting=1

       - keyctl=1

     docker_user: dockeruser

     ```

   - From `group_vars/cap_gpu/vars.yml`:

     ```yaml

     enable_gpu_passthrough: true

     configure_nvidia_runtime: true

     ```

   - From `group_vars/cap_service_agents/vars.yml`:

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

     cores: "{{ lxc_cores }}"

     memory: "{{ lxc_memory }}"

   ```



## Adding a New Host



1. Choose the resource tier based on workload requirements.

2. Select capability groups for the features the container needs.

3. Add the host to `hosts.yml` under the appropriate groups.

4. Create `host_vars/<hostname>.yml` with container specifics.



Example: add a `database` host requiring the large tier and Docker tooling.



```yaml

# hosts.yml

all:

  children:

    tier_large:

      hosts:

        database:

    cap_docker:

      hosts:

        database:

    lxcs:

      hosts:

        database:

```



```yaml

# host_vars/database.yml

---

proxmox_lxc:

  vmid: 305

  hostname: database

  description: "PostgreSQL database server"

  node: "{{ proxmox_default_node }}"

  cores: "{{ lxc_cores }}"

  memory: "{{ lxc_memory }}"

  disk: "local-lvm:{{ lxc_disk }}"

```



## Useful Commands



```powershell

# Visualize the inventory

ansible-inventory --graph



# Inspect variables merged for a host

ansible-inventory --host media --yaml



# List members of a resource tier

ansible-inventory --graph tier_small

```



Validate YAML syntax with `yamllint inventory/` and consult

[docs/inventory-structure-guide.md](../docs/inventory-structure-guide.md) for

additional detail.



Hosts resolve via DNS as `{hostname}.faviann.vms` or through the Proxmox API by

container ID.
