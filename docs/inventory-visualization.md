# Inventory Structure Visualization

## Host Group Memberships

This diagram shows how hosts are organized into resource tiers and functional groups:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Resource Tiers                                  │
│                    (Mutually Exclusive - Pick ONE)                       │
└─────────────────────────────────────────────────────────────────────────┘

    tiny_servers          small_servers         medium_servers        large_servers
   (1c/512MB/8GB)        (2c/2GB/8GB)         (4c/8GB/8GB)          (8c/16GB/8GB)
        │                      │                    │                     │
        │               ┌──────┴──────┐             │                     │
        │               │             │             │                     │
        │          codeserver    frontend         media              jellyfin
        │               │             │             │                     │
        └───────────────┴─────────────┴─────────────┴─────────────────────┘


┌─────────────────────────────────────────────────────────────────────────┐
│                      Functional Groups                                   │
│              (Compositional - Host can be in MULTIPLE)                   │
└─────────────────────────────────────────────────────────────────────────┘

    docker_hosts              gpu_access           wireguard_hosts    service_agents
  (Docker runtime)        (GPU passthrough)         (WireGuard)    (Service tooling)
         │                       │                      │                  │
    ┌────┼────┬────┬────┐       ├───────┐              │          ┌───────┼──────┐
    │    │    │    │    │       │       │              │          │       │      │
codeserver │  media │  media jellyfin              codeserver frontend media
           │       │                                    │
       frontend  jellyfin                               │
                                                    (subset of
                                                   docker_hosts)

```

## Host Configuration Matrix

| Host       | Resource Tier | CPU | RAM   | Docker | GPU | WireGuard | Service Agent | VMID |
|------------|--------------|-----|-------|--------|-----|-----------|---------------|------|
| codeserver | small        | 2   | 2GB   | ✓      | ✗   | ✗         | ✓             | 301  |
| frontend   | small        | 2   | 2GB   | ✓      | ✗   | ✗         | ✓             | 302  |
| media      | medium       | 4   | 8GB   | ✓      | ✓   | ✗         | ✓             | 303  |
| jellyfin   | large        | 8   | 32GB* | ✓      | ✓   | ✗         | ✗             | 304  |

*jellyfin has a resource override: 32GB RAM instead of the large_servers default 16GB

## Variable Flow Diagram

This shows how variables flow from groups to a specific host (`media` example):

```
                    ┌──────────────────────────┐
                    │   group_vars/all/        │
                    │   proxmox.yml            │
                    │                          │
                    │ • proxmox_api_host       │
                    │ • proxmox_default_node   │
                    │ • proxmox_default_mounts │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │ group_vars/              │
                    │ medium_servers.yml       │
                    │                          │
                    │ • lxc_cores: 4           │
                    │ • lxc_memory: 8192       │
                    │ • lxc_disk: "8"          │
                    └────────────┬─────────────┘
                                 │
                ┌────────────────┼────────────────┐
                │                │                │
                ▼                ▼                ▼
    ┌───────────────────┐ ┌───────────────┐ ┌──────────────────┐
    │ group_vars/       │ │ group_vars/   │ │ group_vars/      │
    │ docker_hosts.yml  │ │ gpu_access.yml│ │ service_agents.yml│
    │                   │ │               │ │                  │
    │ • install_docker  │ │ • enable_gpu  │ │ • traefik_kop    │
    │ • lxc_features    │ │ • nvidia_cfg  │ │ • socket_proxy   │
    │ • docker_user     │ │               │ │ • dockwatch      │
    └─────────┬─────────┘ └───────┬───────┘ └────────┬─────────┘
              │                   │                  │
              └───────────────────┼──────────────────┘
                                  │
                                  ▼
                    ┌──────────────────────────┐
                    │ host_vars/media.yml      │
                    │                          │
                    │ • vmid: 303              │
                    │ • hostname: media        │
                    │ • cores: "{{ lxc_cores }}"│  ← Uses 4 from medium_servers
                    │ • memory: "{{ lxc_memory }}"│ ← Uses 8192 from medium_servers
                    └──────────────────────────┘
                                  │
                                  ▼
                    ┌──────────────────────────┐
                    │   Final Host Config      │
                    │        (media)           │
                    │                          │
                    │ Merged variables from:   │
                    │ • all/proxmox.yml        │
                    │ • medium_servers.yml     │
                    │ • docker_hosts.yml       │
                    │ • gpu_access.yml         │
                    │ • service_agents.yml     │
                    │ • host_vars/media.yml    │
                    └──────────────────────────┘
```

## Group Relationships

```
                        ┌─────────────────┐
                        │   docker_hosts  │
                        │                 │
                        │ All LXCs with   │
                        │ Docker runtime  │
                        └────────┬────────┘
                                 │
                                 │ inherits from
                                 │
                        ┌────────▼────────┐
                        │ service_agents  │
                        │                 │
                        │ Subset with     │
                        │ service tooling │
                        └─────────────────┘

    codeserver, frontend, media           codeserver, frontend, media
       (all docker_hosts)                    (all service_agents)

         jellyfin                                  ---
    (docker_host but NOT                     (jellyfin excluded -
     a service_agent)                      doesn't need traefik tools)
```

## Provisioning Workflow

```
┌──────────────┐
│ Control Node │
│              │
│ Runs Ansible │
└──────┬───────┘
       │
       │ 1. Connects to Proxmox API
       │
       ▼
┌──────────────────┐
│ Proxmox Host     │
│                  │
│ API Endpoint     │◄─── Uses proxmox_api group vars
└──────┬───────────┘
       │
       │ 2. Provisions LXC using:
       │    • Resource tier vars (CPU, RAM, disk)
       │    • Host vars (vmid, hostname)
       │
       ▼
┌──────────────────┐
│ LXC Container    │
│                  │
│ (e.g., media)    │◄─── Created with merged variables
└──────┬───────────┘
       │
       │ 3. Configure LXC using:
       │    • Functional group vars
       │    • (Docker, GPU, etc.)
       │
       ▼
┌──────────────────┐
│ Configured LXC   │
│                  │
│ Ready to use     │
└──────────────────┘
```

## Exception Handling Pattern

Example: jellyfin needs extra RAM beyond its resource tier default

```
┌────────────────────────────┐
│ group_vars/large_servers   │
│                            │
│ lxc_cores: 8               │
│ lxc_memory: 16384  ◄───────┼─── Default for all large_servers
│ lxc_disk: "8"              │
└────────────┬───────────────┘
             │
             │ Inherited by jellyfin
             │
             ▼
┌────────────────────────────┐
│ host_vars/jellyfin.yml     │
│                            │
│ proxmox_lxc:               │
│   cores: "{{ lxc_cores }}" │◄─── Uses group default: 8
│   memory: 32768            │◄─── OVERRIDE: 32GB instead of 16GB
│   disk: "{{ lxc_disk }}"   │◄─── Uses group default: "8"
└────────────────────────────┘
             │
             ▼
┌────────────────────────────┐
│ Final jellyfin config:     │
│                            │
│ • 8 CPU cores (inherited)  │
│ • 32GB RAM (overridden)    │
│ • 8GB disk (inherited)     │
└────────────────────────────┘
```

## Scaling Strategy

### Small Scale (Current: 4 hosts)
```
inventory/
├── hosts.yml (all hosts in one file)
└── group_vars/ (resource + functional groups)
```

### Medium Scale (10-50 hosts)
```
inventory/
├── production.yml
├── staging.yml
├── group_vars/
│   ├── production/
│   └── staging/
└── host_vars/
```

### Large Scale (50+ hosts)
```
Use dynamic inventory via Proxmox API
+ Keep static inventory for core services only
+ Automate host_vars generation
+ Consider Ansible Tower/AWX
```

## Key Design Principles

1. **Resource Tiers are Mutually Exclusive**
   - Each host belongs to exactly ONE resource tier
   - Defines CPU, RAM, disk allocations

2. **Functional Groups are Compositional**
   - Hosts can belong to MULTIPLE functional groups
   - Each group adds specific capabilities

3. **Exceptions via host_vars**
   - Override specific values when needed
   - Document the reason for each exception

4. **Variables Reference Groups**
   - Use `"{{ lxc_cores }}"` instead of hardcoded values
   - Makes overrides explicit and visible

5. **Service Agents ⊂ Docker Hosts**
   - All service_agents must be docker_hosts
   - Not all docker_hosts are service_agents
   - Example: jellyfin is docker_host but NOT service_agent
