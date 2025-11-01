---
# Ansible Inventory Structure - Best Practices and Guidelines

## Overview

This document describes the resource-based inventory structure for managing Proxmox LXC containers. The design follows Ansible best practices for scalability, clarity, and maintainability.

## Directory Structure

```
inventory/
├── hosts.yml                           # Main inventory file
├── group_vars/                         # Group-level variables
│   ├── all/                           # Variables for all hosts
│   │   ├── proxmox.yml               # Proxmox API and infrastructure config
│   │   └── vault.yml                 # Encrypted secrets (API tokens, etc.)
│   ├── proxmox_api/vars.yml          # API controller configuration
│   │
│   ├── tier_tiny/vars.yml         # Resource tier: 1 core, 512MB RAM
│   ├── tier_small/vars.yml        # Resource tier: 2 cores, 2GB RAM
│   ├── tier_medium/vars.yml       # Resource tier: 4 cores, 8GB RAM
│   ├── tier_large/vars.yml        # Resource tier: 8 cores, 16GB RAM
│   │
│   ├── cap_docker/vars.yml        # Functional: Docker installation
│   ├── cap_gpu/vars.yml           # Functional: GPU passthrough
│   ├── cap_wireguard/vars.yml     # Functional: WireGuard VPN
│   └── cap_service_agents/vars.yml      # Functional: Service management tools
│
└── host_vars/                         # Host-specific variables
    ├── codeserver.yml                # VSCode server configuration
    ├── frontend.yml                  # Frontend service configuration
    ├── media.yml                     # Media processing configuration
    └── jellyfin.yml                  # Jellyfin (with resource overrides)
```

## Design Principles

### 1. Resource Tiers (Mutually Exclusive)

Each host belongs to **exactly ONE** resource tier group that defines its compute resources:

- **tier_tiny**: Lightweight services (1 core, 512MB RAM, 8GB disk)
- **tier_small**: Small applications (2 cores, 2GB RAM, 8GB disk)
- **tier_medium**: Medium workloads (4 cores, 8GB RAM, 8GB disk)
- **tier_large**: Resource-intensive apps (8 cores, 16GB RAM, 8GB disk)

All tiers use the same network bridge (`vmbr1`) by default.

### 2. Functional Groups (Compositional/Additive)

Hosts can belong to **MULTIPLE** functional groups based on capabilities needed:

- **cap_docker**: LXCs with Docker runtime and compose
- **cap_gpu**: LXCs with GPU passthrough for hardware acceleration
- **cap_wireguard**: LXCs with WireGuard kernel module access
- **cap_service_agents**: Subset of cap_docker with additional tooling:
  - traefik-kop (Traefik Kubernetes Operator)
  - traefik-socket-proxy (Docker socket security proxy)
  - dockwatch (Container monitoring and updates)

### 3. Exception Handling

Resource overrides for special cases are handled in `host_vars/`:

```yaml
# Example: jellyfin.yml overrides memory allocation
proxmox_lxc:
  cores: "{{ lxc_cores }}"      # Inherit from group
  memory: 32768                  # Override: 32GB instead of 16GB default
```

Use overrides **sparingly** and document the reason for each exception.

## Variable Precedence

Ansible applies variables in this order (later sources override earlier ones):

1. **group_vars/all/**: Base configuration for all hosts
2. **group_vars/{resource_tier}.yml**: Resource specifications
3. **group_vars/{functional_group}.yml**: Functional capabilities
4. **host_vars/{hostname}.yml**: Host-specific overrides

### Example: Variable Inheritance

For a host named `media` in `tier_medium`, `cap_docker`, `cap_gpu`, and `cap_service_agents`:

```yaml
# Inherited variables (in merge order):
# 1. group_vars/all/proxmox.yml
proxmox_api_host: "proxmox.internal.faviann.com"
proxmox_default_node: "proxmox"
proxmox_default_mounts: { ... }

# 2. group_vars/tier_medium/vars.yml
lxc_cores: 4
lxc_memory: 8192
lxc_disk: "8"
lxc_network_bridge: vmbr1

# 3. group_vars/cap_docker/vars.yml
install_docker: true
docker_user: dockeruser
lxc_features: [nesting=1, keyctl=1]

# 4. group_vars/cap_gpu/vars.yml
enable_gpu_passthrough: true
configure_nvidia_runtime: true

# 5. group_vars/cap_service_agents/vars.yml
configure_traefik_kop: true
configure_traefik_socket_proxy: true
configure_dockwatch: true

# 6. host_vars/media.yml (can override any of the above)
proxmox_lxc:
  vmid: 303
  hostname: media
  cores: "{{ lxc_cores }}"      # Uses inherited value: 4
  memory: "{{ lxc_memory }}"    # Uses inherited value: 8192
```

## Adding New Hosts

### Step 1: Determine Resource Tier

Choose the appropriate tier based on workload requirements:
- Tiny: Monitoring agents, lightweight proxies
- Small: Development tools, small web services
- Medium: Application servers, media processing
- Large: Database servers, media servers with transcoding

### Step 2: Determine Functional Groups

Select all applicable functional groups:
- Does it run Docker? → Add to `cap_docker`
- Does it need GPU? → Add to `cap_gpu`
- Does it need VPN? → Add to `cap_wireguard`
- Is it a Docker service agent? → Add to `cap_service_agents`

### Step 3: Add to Inventory

Edit `inventory/hosts.yml`:

```yaml
tier_small:
  hosts:
    mynewhost:
      proxmox_lxc:
        vmid: 305
        hostname: mynewhost
        description: "My new service"

cap_docker:
  hosts:
    mynewhost:

cap_service_agents:
  hosts:
    mynewhost:
```

### Step 4: Create Host Variables

Create `inventory/host_vars/mynewhost.yml`:

```yaml
---
# Host-specific configuration for mynewhost
# Inherits from: tier_small, cap_docker, cap_service_agents

proxmox_lxc_overrides:
  vmid: 305
  hostname: mynewhost
  description: "My new service managed via Ansible"
  tags:
    - ansible
    - mynewhost
```

## Best Practices

### 1. Use Templates for Common Patterns

Most host_vars files now only declare what is unique to the host. Copy an existing file and edit the override map:
- Use `codeserver.yml` as template for small docker hosts
- Use `jellyfin.yml` as template for large hosts with resource overrides

### 2. Document Exceptions

When overriding resource allocations, add a comment explaining why:

```yaml
# OVERRIDE: Extra memory for heavy transcoding workload
proxmox_lxc_overrides:
  memory: 32768  # 32GB instead of tier_large default 16GB
```

### 3. Keep Defaults Generic

Variables in `group_vars/` should represent sensible defaults, not host-specific values. Host-specific tweaks belong in `proxmox_lxc_overrides`.

### 4. Override Only What You Need

Leave CPU, memory, disk, network, and mount settings out of host_vars unless they genuinely differ. The provisioning role builds those from tier and capability groups automatically.

### 5. Organize Functional Groups Logically

- Most Docker hosts will also be cap_service_agents (exceptions like reverse-proxy nodes)
- All cap_service_agents should be in cap_docker
- GPU access is typically independent of other functional groups

### 6. Network Resolution

Hosts will be resolved via DNS as `{hostname}.faviann.vms` or through Proxmox API using their container ID or name.

## Workflow Integration

### Provisioning Workflow

1. **Control node** connects to Proxmox API (`proxmox_api` group)
2. Playbook provisions LXC using variables from:
   - Resource tier (cores, memory, disk)
   - Functional groups (features, capabilities)
   - Host vars (vmid, hostname, specific config)
3. **After provisioning**, control node can connect to LXC via SSH for configuration
4. Configuration playbooks use functional group variables to install software:
   - `cap_docker` → Install Docker
   - `cap_gpu` → Configure GPU passthrough
   - `cap_service_agents` → Deploy traefik-kop, socket-proxy, dockwatch

### Example Playbook Targets

```yaml
# Provision all small servers
- hosts: tier_small
  roles:
    - proxmox_lxc_provision

# Configure all Docker hosts
- hosts: cap_docker
  roles:
    - docker_install

# Configure service agents
- hosts: cap_service_agents
  roles:
    - traefik_kop
    - traefik_socket_proxy
    - dockwatch
```

## Variable Conflict Resolution

### Potential Conflicts

When a variable is defined in multiple groups, Ansible's merge order determines the final value. The order is **alphabetical by group name** for groups at the same level, then overridden by host_vars.

### Avoiding Conflicts

1. **Use unique variable names per group**
   ```yaml
   # Good: Prefixed variable names
   docker_user: dockeruser           # in cap_docker.yml
   wireguard_interface: wg0          # in cap_wireguard.yml
   
   # Avoid: Generic names that might conflict
   user: dockeruser
   interface: wg0
   ```

2. **Use nested dictionaries for complex config**
   ```yaml
   # Good: Nested structure
   lxc_features:
     - nesting=1
     - keyctl=1
   
   # Better: Merging dictionaries
   lxc_config:
     docker:
       nesting: true
       keyctl: true
     gpu:
       device_passthrough: true
   ```

3. **Document merge behavior in group_vars**
   ```yaml
   # This variable merges with cap_docker.lxc_features
   lxc_features:
     - nesting=1
     - keyctl=1
   ```

## Scaling Guidance

### Small Scale (1-10 hosts)
- Current structure is sufficient
- All hosts can be in `hosts.yml`
- Keep group_vars as-is

### Medium Scale (10-50 hosts)
- Consider splitting `hosts.yml` by function:
  - `inventory/production.yml`
  - `inventory/staging.yml`
- Add environment-specific group_vars:
  - `group_vars/production/`
  - `group_vars/staging/`

### Large Scale (50+ hosts)
- Use dynamic inventory via Proxmox API
- Keep static inventory for core services only
- Add automation for host_vars generation
- Consider Ansible Tower/AWX for centralized management

## Migration from Legacy Structure

If migrating from the previous structure:

1. **Map old variables to new groups**
   - `disk_size`, `ram_size`, `core_count` → Resource tier variables
   - `enable_nvidia`, `enable_wireguard` → Functional group membership

2. **Move host-specific config to host_vars**
   - Each host in old `lxc_containers` → New host_vars file
   - Reference group variables instead of hardcoded values

3. **Update playbooks**
   - Change group targets from `lxc_containers` to resource/functional groups
   - Use new variable names (`lxc_cores` vs `core_count`)

4. **Test incrementally**
   - Validate syntax: `yamllint inventory/`
   - Check variable resolution: `ansible-inventory --host <hostname> --yaml`
   - Provision test host before migrating production hosts

## Validation and Testing

### Syntax Validation

```bash
# Validate YAML syntax
yamllint inventory/

# Check inventory structure
ansible-inventory --list --yaml

# View variables for specific host
ansible-inventory --host codeserver --yaml
```

### Variable Inspection

```bash
# Show all variables for a host
ansible-inventory --host media --yaml

# Show group membership
ansible-inventory --graph

# Debug variable precedence
ansible -m debug -a "var=hostvars[inventory_hostname]" media
```

### Test Provisioning

```bash
# Dry-run mode (check mode)
ansible-playbook playbooks/lxc-provision.yml --check --diff

# Provision single host
ansible-playbook playbooks/lxc-provision.yml --limit codeserver

# Provision by resource tier
ansible-playbook playbooks/lxc-provision.yml --limit tier_small
```

## Troubleshooting

### Variables Not Applied

**Problem**: Host not getting expected variables from group
**Solution**: Check group membership in `hosts.yml` and verify YAML indentation

### Variable Override Not Working

**Problem**: host_vars value not overriding group_vars
**Solution**: Ensure variable name matches exactly (case-sensitive)

### Unexpected Variable Value

**Problem**: Variable has unexpected value from wrong group
**Solution**: Use `ansible-inventory --host <name> --yaml` to trace variable sources

## Additional Resources

- [Ansible Inventory Documentation](https://docs.ansible.com/ansible/latest/inventory_guide/)
- [Variable Precedence](https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_variables.html#variable-precedence-where-should-i-put-a-variable)
- [Group Variables](https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html#organizing-host-and-group-variables)
