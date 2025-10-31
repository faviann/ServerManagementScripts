# Migration Guide: Legacy to Resource-Based Inventory

This guide helps you migrate from the legacy inventory structure (archived in `archive/ansible/`) to the new resource-based inventory structure.

## Overview of Changes

### Old Structure (Archive)
```
archive/ansible/
├── inventory/
│   └── hosts.yml               # Simple flat host list
├── group_vars/
│   └── all.yml                 # All variables in one file
└── host_vars/
    ├── example-app.yml
    └── gpu-app.yml
```

### New Structure
```
inventory/
├── hosts.yml                   # Hosts organized by resource tiers + functional groups
├── group_vars/
│   ├── all/proxmox.yml        # Proxmox infrastructure config
│   ├── {tier}_servers.yml     # Resource tier specifications
│   └── {function}.yml         # Functional capabilities
└── host_vars/
    └── {hostname}.yml         # Host-specific config
```

## Variable Mapping

### Resource Variables

Old variables in `archive/ansible/group_vars/all.yml` → New resource tier files:

| Old Variable | New Variable | Location |
|-------------|-------------|----------|
| `disk_size: 8` | `lxc_disk: "8"` | `group_vars/{tier}_servers.yml` |
| `ram_size: 8192` | `lxc_memory: 8192` | `group_vars/{tier}_servers.yml` |
| `core_count: 4` | `lxc_cores: 4` | `group_vars/{tier}_servers.yml` |
| `swap_size: 8192` | `lxc_swap: 8192` | `group_vars/{tier}_servers.yml` |
| `network_bridge: vmbr1` | `lxc_network_bridge: vmbr1` | `group_vars/{tier}_servers.yml` |

### Functional Variables

Old feature flags → New functional group membership:

| Old Variable | New Approach | Location |
|-------------|-------------|----------|
| `enable_nvidia: true` | Add to `gpu_access` group | `hosts.yml` + `group_vars/gpu_access.yml` |
| `enable_wireguard: true` | Add to `wireguard_hosts` group | `hosts.yml` + `group_vars/wireguard_hosts.yml` |
| `enable_docker: true` | Add to `docker_hosts` group | `hosts.yml` + `group_vars/docker_hosts.yml` |

### Infrastructure Variables

Old `archive/ansible/group_vars/all.yml` → New `inventory/group_vars/all/proxmox.yml`:

| Old Variable | New Variable | Notes |
|-------------|-------------|-------|
| `mp0_storage` | `proxmox_master_lxc_storage` | More descriptive name |
| `mp0_volume` | Part of `proxmox_default_mounts` | Structured as dict |
| `uid_gid_mappings` | `proxmox_default_idmap` | Consistent naming |

## Migration Steps

### Step 1: Inventory Host Mapping

For each host in the old inventory, determine:

1. **Which resource tier?** (Look at `ram_size`, `core_count`)
   - 512MB RAM → `tiny_servers`
   - 2GB RAM → `small_servers`
   - 8GB RAM → `medium_servers`
   - 16GB+ RAM → `large_servers`

2. **Which functional groups?** (Look at feature flags)
   - `enable_docker: true` → Add to `docker_hosts`
   - `enable_nvidia: true` → Add to `gpu_access`
   - `enable_wireguard: true` → Add to `wireguard_hosts`
   - Docker + needs service tooling → Add to `service_agents`

### Step 2: Create Host Entries

#### Example: Migrating `archive/ansible/inventory/hosts.yml`

**Old:**
```yaml
lxc_containers:
  hosts:
    example-app:
      lxc_id: 201
    gpu-app:
      lxc_id: 202
```

**New:**
```yaml
small_servers:
  hosts:
    example-app:
      proxmox_lxc:
        vmid: 201
        hostname: example-app

large_servers:
  hosts:
    gpu-app:
      proxmox_lxc:
        vmid: 202
        hostname: gpu-app

docker_hosts:
  hosts:
    example-app:
    gpu-app:

gpu_access:
  hosts:
    gpu-app:
```

### Step 3: Migrate Host Variables

#### Example: Migrating `archive/ansible/host_vars/example-app.yml`

**Old:**
```yaml
# host_vars/example-app.yml
lxc_id: 201
disk_size: 8
ram_size: 2048
core_count: 2
network_bridge: vmbr1
enable_docker: true
```

**New:**
```yaml
---
# host_vars/example-app.yml
# Inherits from: small_servers, docker_hosts

proxmox_lxc:
  vmid: 201
  hostname: example-app
  description: "Example application"
  node: "{{ proxmox_default_node }}"
  ostemplate: "local:/var/lib/vz/template/cache/debian-13-standard_13.1-2_amd64.tar.zst"
  pool: homelab
  cores: "{{ lxc_cores }}"              # Inherited: 2 from small_servers
  memory: "{{ lxc_memory }}"            # Inherited: 2048 from small_servers
  swap: "{{ lxc_swap | default(512) }}" # Inherited: 512 from small_servers
  disk: "local-lvm:{{ lxc_disk }}"      # Inherited: "8" from small_servers
  netif:
    net0: "name=eth0,bridge={{ lxc_network_bridge }},firewall=0,ip=dhcp,ip6=auto,type=veth"
  onboot: true
  unprivileged: true
  features: "{{ lxc_features | default([]) }}"  # Inherited from docker_hosts
  tags:
    - ansible
    - example

proxmox_lxc_start_on_create: true
proxmox_lxc_mounts: "{{ proxmox_default_mounts | combine({}) }}"
proxmox_lxc_idmap: "{{ proxmox_default_idmap | list }}"
```

### Step 4: Handle Resource Overrides

If a host had custom resource values different from its tier:

**Old:**
```yaml
# host_vars/special-app.yml
ram_size: 32768  # More than large_servers default
```

**New:**
```yaml
# host_vars/special-app.yml
# Still in large_servers group, but override memory
proxmox_lxc:
  cores: "{{ lxc_cores }}"    # Inherit from group
  memory: 32768               # OVERRIDE: 32GB instead of 16GB
  disk: "{{ lxc_disk }}"      # Inherit from group

# Add comment explaining why
# OVERRIDE: Extra memory needed for heavy processing workload
```

## Specific Examples

### Example 1: Simple Docker Host

**Old structure:**
```yaml
# archive/ansible/inventory/hosts.yml
lxc_containers:
  hosts:
    myapp:
      lxc_id: 210

# archive/ansible/host_vars/myapp.yml
lxc_id: 210
disk_size: 8
ram_size: 2048
core_count: 2
enable_docker: true
```

**New structure:**
```yaml
# inventory/hosts.yml
small_servers:
  hosts:
    myapp:

docker_hosts:
  hosts:
    myapp:

lxcs:
  hosts:
    myapp:

# inventory/host_vars/myapp.yml
---
# Inherits from: small_servers, docker_hosts
proxmox_lxc:
  vmid: 210
  hostname: myapp
  description: "My application"
  node: "{{ proxmox_default_node }}"
  cores: "{{ lxc_cores }}"
  memory: "{{ lxc_memory }}"
  # ... standard config
```

### Example 2: GPU-Enabled Media Server

**Old structure:**
```yaml
# archive/ansible/inventory/hosts.yml
lxc_containers:
  hosts:
    plex:
      lxc_id: 220

# archive/ansible/host_vars/plex.yml
lxc_id: 220
disk_size: 8
ram_size: 16384
core_count: 8
enable_docker: true
enable_nvidia: true
```

**New structure:**
```yaml
# inventory/hosts.yml
large_servers:
  hosts:
    plex:

docker_hosts:
  hosts:
    plex:

gpu_access:
  hosts:
    plex:

lxcs:
  hosts:
    plex:

# inventory/host_vars/plex.yml
---
# Inherits from: large_servers, docker_hosts, gpu_access
proxmox_lxc:
  vmid: 220
  hostname: plex
  description: "Plex media server with GPU transcoding"
  node: "{{ proxmox_default_node }}"
  cores: "{{ lxc_cores }}"      # 8 from large_servers
  memory: "{{ lxc_memory }}"    # 16384 from large_servers
  # ... standard config
  tags:
    - ansible
    - plex
    - gpu
```

## Validation After Migration

### 1. YAML Syntax
```bash
yamllint inventory/
```

### 2. Inventory Structure
```bash
ansible-inventory --graph
```

Expected output should show:
- Resource tier groups with hosts
- Functional groups with hosts
- Each host appearing in multiple groups

### 3. Variable Resolution
```bash
ansible-inventory --host <hostname> --yaml
```

Verify:
- Resource variables (cores, memory, disk) match expected tier
- Functional variables (docker, gpu, etc.) are present
- No unexpected variable conflicts

### 4. Test Playbook (Dry Run)
```bash
ansible-playbook playbooks/lxc-provision.yml --check --diff --limit <test-host>
```

## Common Migration Issues

### Issue 1: Variable Name Mismatches

**Problem:** Playbooks expect old variable names
```yaml
# Old playbook references
cores: "{{ core_count }}"
```

**Solution:** Update playbooks or add variable aliases in `group_vars/all/`:
```yaml
# Backwards compatibility (temporary)
core_count: "{{ lxc_cores }}"
ram_size: "{{ lxc_memory }}"
```

### Issue 2: Missing Group Membership

**Problem:** Host not getting expected variables

**Solution:** Check group membership in `hosts.yml`:
```bash
ansible-inventory --host problematic-host --yaml | grep -A 20 "groups:"
```

### Issue 3: Variable Override Not Working

**Problem:** Host_vars value not overriding group_vars

**Solution:** Ensure exact variable name match (case-sensitive):
```yaml
# Wrong - won't override
lxc_memory: 32768

# Correct - overrides the value inside proxmox_lxc dict
proxmox_lxc:
  memory: 32768
```

## Rollback Plan

If you need to temporarily rollback:

1. **Keep archived structure intact** during migration
2. **Switch Ansible config** to point to archive:
   ```ini
   # ansible.cfg
   [defaults]
   inventory = archive/ansible/inventory/hosts.yml
   ```
3. **Test new structure in parallel** using a different inventory file
4. **Gradually migrate** one host at a time

## Migration Checklist

- [ ] Identify all hosts in old inventory
- [ ] Map each host to resource tier and functional groups
- [ ] Create new host entries in `inventory/hosts.yml`
- [ ] Create `host_vars/` files for each host
- [ ] Document any resource overrides with comments
- [ ] Validate YAML syntax with `yamllint`
- [ ] Test inventory structure with `ansible-inventory`
- [ ] Verify variable resolution for each host
- [ ] Run test playbooks with `--check` mode
- [ ] Update playbooks to use new variable names if needed
- [ ] Update documentation and runbooks
- [ ] Archive old inventory structure

## Best Practices for Migration

1. **Migrate incrementally** - Start with one or two hosts
2. **Test thoroughly** - Use `--check` mode before applying
3. **Document differences** - Note any special configurations
4. **Keep both structures** during transition period
5. **Use version control** - Commit after each successful migration
6. **Validate variables** - Check resolution with `ansible-inventory`

## Getting Help

If you encounter issues during migration:

1. Check variable resolution: `ansible-inventory --host <hostname> --yaml`
2. Verify group membership: `ansible-inventory --graph`
3. Review documentation: `docs/inventory-structure-guide.md`
4. Test in check mode: `ansible-playbook ... --check`

## Next Steps After Migration

Once migration is complete:

1. Update playbooks to use new group targets
2. Update documentation and runbooks
3. Train team on new structure
4. Archive old inventory (already in `archive/`)
5. Consider adding more functional groups as needed
6. Document any custom patterns for your environment
