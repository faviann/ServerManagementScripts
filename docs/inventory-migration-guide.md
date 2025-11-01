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
| `ram_size: 8192` | `lxc_memory: 8192` | `group_vars/tier_<tier>/vars.yml` |
| `core_count: 4` | `lxc_cores: 4` | `group_vars/tier_<tier>/vars.yml` |
| `swap_size: 8192` | `lxc_swap: 8192` | `group_vars/tier_<tier>/vars.yml` |
| `network_bridge: vmbr1` | `lxc_network_bridge: vmbr1` | `group_vars/tier_<tier>/vars.yml` |

### Functional Variables

Old feature flags → New functional group membership:

| Old Variable | New Approach | Location |
|-------------|-------------|----------|
| `enable_nvidia: true` | Add to `cap_gpu` group | `hosts.yml` + `group_vars/cap_gpu/vars.yml` |
| `enable_wireguard: true` | Add to `cap_wireguard` group | `hosts.yml` + `group_vars/cap_wireguard/vars.yml` |
| `enable_docker: true` | Add to `cap_docker` group | `hosts.yml` + `group_vars/cap_docker/vars.yml` |

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
   - 512MB RAM → `tier_tiny`
   - 2GB RAM → `tier_small`
   - 8GB RAM → `tier_medium`
   - 16GB+ RAM → `tier_large`

2. **Which functional groups?** (Look at feature flags)
   - `enable_docker: true` → Add to `cap_docker`
   - `enable_nvidia: true` → Add to `cap_gpu`
   - `enable_wireguard: true` → Add to `cap_wireguard`
   - Docker + needs service tooling → Add to `cap_service_agents`

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
tier_small:
  hosts:
    example-app:
      proxmox_lxc:
        vmid: 201
        hostname: example-app

tier_large:
  hosts:
    gpu-app:
      proxmox_lxc:
        vmid: 202
        hostname: gpu-app

cap_docker:
  hosts:
    example-app:
    gpu-app:

cap_gpu:
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
# Inherits from: tier_small, cap_docker

proxmox_lxc_overrides:
  vmid: 201
  hostname: example-app
  description: "Example application"
  tags:
    - ansible
    - example
```

### Step 4: Handle Resource Overrides

If a host had custom resource values different from its tier:

**Old:**
```yaml
# host_vars/special-app.yml
ram_size: 32768  # More than tier_large default
```

**New:**
```yaml
# host_vars/special-app.yml
# Still in tier_large group, but override memory
proxmox_lxc_overrides:
  memory: 32768  # OVERRIDE: 32GB instead of 16GB

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
tier_small:
  hosts:
    myapp:

cap_docker:
  hosts:
    myapp:

lxcs:
  hosts:
    myapp:

# inventory/host_vars/myapp.yml
---
# Inherits from: tier_small, cap_docker
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
tier_large:
  hosts:
    plex:

cap_docker:
  hosts:
    plex:

cap_gpu:
  hosts:
    plex:

lxcs:
  hosts:
    plex:

# inventory/host_vars/plex.yml
---
# Inherits from: tier_large, cap_docker, cap_gpu
proxmox_lxc:
  vmid: 220
  hostname: plex
  description: "Plex media server with GPU transcoding"
  node: "{{ proxmox_default_node }}"
  cores: "{{ lxc_cores }}"      # 8 from tier_large
  memory: "{{ lxc_memory }}"    # 16384 from tier_large
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
