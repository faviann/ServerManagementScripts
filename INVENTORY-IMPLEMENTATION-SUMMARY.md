# Ansible Inventory Restructure - Implementation Summary

## Overview

This implementation provides a resource-based inventory structure for managing Proxmox LXC containers using Ansible. The design follows best practices for scalability, clarity, and maintainability.

## What Was Implemented

### 1. Resource Tier Groups (4 tiers)

Created group_vars files for four resource tiers:

| Tier | CPU | RAM | Disk | File |
|------|-----|-----|------|------|
| tiny | 1 core | 512 MB | 8 GB | `inventory/group_vars/tier_tiny/vars.yml` |
| small | 2 cores | 2 GB | 8 GB | `inventory/group_vars/tier_small/vars.yml` |
| medium | 4 cores | 8 GB | 8 GB | `inventory/group_vars/tier_medium/vars.yml` |
| large | 8 cores | 16 GB | 8 GB | `inventory/group_vars/tier_large/vars.yml` |

All tiers use network bridge `vmbr1` by default.

### 2. Functional Groups (4 groups)

Created group_vars files for functional capabilities:

| Group | Purpose | Key Variables |
|-------|---------|---------------|
| `cap_docker` | Docker runtime and compose | `install_docker`, `lxc_features`, `docker_user` |
| `cap_gpu` | GPU passthrough capabilities | `enable_gpu_passthrough`, `configure_nvidia_runtime` |
| `cap_wireguard` | WireGuard VPN kernel module | `enable_wireguard`, `wireguard_kernel_module_access` |
| `cap_service_agents` | Service management tools | `configure_traefik_kop`, `configure_traefik_socket_proxy`, `configure_dockwatch` |

**Note:** `cap_service_agents` is designed as a subset of `cap_docker` for hosts that need additional service tooling.

### 3. Updated Inventory Structure

File: `inventory/hosts.yml`

```
Resource Tiers:
  - tier_tiny: (empty - placeholder for future hosts)
  - tier_small: codeserver, frontend
  - tier_medium: media
  - tier_large: jellyfin

Capability Groups:
  - cap_docker: codeserver, frontend, media, jellyfin
  - cap_gpu: media, jellyfin
  - cap_wireguard: (empty - placeholder)
  - cap_service_agents: codeserver, frontend, media
    (Note: jellyfin is intentionally excluded - doesn't need traefik tools)
```

### 4. Example Host Variables

Created host_vars files for four example hosts:

| Host | VMID | Resource Tier | Functional Groups | Special Notes |
|------|------|---------------|-------------------|---------------|
| codeserver | 301 | small | cap_docker, cap_service_agents | VSCode development server |
| frontend | 302 | small | cap_docker, cap_service_agents | Frontend web service |
| media | 303 | medium | cap_docker, cap_gpu, cap_service_agents | Media processing with GPU |
| jellyfin | 304 | large | cap_docker, cap_gpu | **Override:** 32GB RAM instead of 16GB |

```yaml
proxmox_lxc:
  cores: "{{ lxc_cores }}"      # Inherit from tier_large (8)
  memory: 32768                  # OVERRIDE: 32GB instead of 16GB default
  disk: "{{ lxc_disk }}"        # Inherit from tier_large ("8")
```

This shows how to override specific resource values while still using variable references for clarity.

3. group_vars/cap_docker/vars.yml     → install_docker: true, lxc_features: [nesting=1, keyctl=1]
4. group_vars/cap_gpu/vars.yml        → enable_gpu_passthrough: true
5. group_vars/cap_service_agents/vars.yml   → configure_traefik_kop: true, etc.

1. **`docs/inventory-structure-guide.md`** (12.7 KB)
   - Complete guide to the inventory structure
   - Variable precedence and inheritance
   - Best practices and scaling guidance
   - Step-by-step instructions for adding hosts
   - Troubleshooting and validation commands

2. **`docs/inventory-visualization.md`** (9.9 KB)
   - ASCII diagrams showing host-group relationships
   - Group relationships and provisioning workflow
   - Exception handling patterns

3. **`docs/inventory-migration-guide.md`** (10.5 KB)
   - Complete migration guide from legacy structure
   - Variable mapping tables
   - Step-by-step migration process
   - Common issues and solutions
   - Rollback plan

4. **`inventory/README.md`** (5.7 KB)
   - Quick reference for the inventory structure
   - Resource tier and functional group tables
   - Current hosts overview
   - Testing commands
- Fixed line length issues by using YAML folded scalars
- Removed trailing spaces and extra blank lines

## Variable Inheritance Examples

### Example 1: media host

```yaml
# Inheritance chain:
1. group_vars/all/proxmox.yml      → proxmox_api_host, proxmox_default_node, etc.
2. group_vars/tier_medium/vars.yml   → lxc_cores: 4, lxc_memory: 8192
3. group_vars/cap_docker/vars.yml     → install_docker: true, lxc_features: [nesting=1, keyctl=1]
4. group_vars/cap_gpu/vars.yml        → enable_gpu_passthrough: true
5. group_vars/cap_service_agents/vars.yml   → configure_traefik_kop: true, etc.
6. host_vars/media.yml             → vmid: 303, hostname: media, etc.
```

**Result:** media host has 4 cores, 8GB RAM, Docker, GPU access, and service agent tools.

### Example 2: jellyfin host

```yaml
# Inheritance chain:
1. group_vars/all/proxmox.yml      → proxmox_api_host, proxmox_default_node, etc.
2. group_vars/tier_large/vars.yml    → lxc_cores: 8, lxc_memory: 16384
3. group_vars/cap_docker/vars.yml     → install_docker: true, lxc_features: [nesting=1, keyctl=1]
4. group_vars/cap_gpu/vars.yml       → enable_gpu_passthrough: true
5. host_vars/jellyfin.yml          → vmid: 304, memory: 32768 (OVERRIDE)
```

**Result:** jellyfin host has 8 cores, **32GB RAM** (overridden), Docker, GPU access, but NO service agent tools.

## Validation Results

All validations passed:

1. **YAML Syntax:** ✓ Passed `yamllint inventory/` with no errors
2. **Inventory Structure:** ✓ Confirmed with `ansible-inventory --graph`
3. **Variable Resolution:** ✓ Verified with `ansible-inventory --host <hostname> --yaml`

### Confirmed Behaviors

- ✓ Hosts are correctly assigned to resource tiers
- ✓ Hosts can belong to multiple functional groups
- ✓ Variables are inherited correctly from groups
- ✓ Resource overrides work as expected (jellyfin memory)
- ✓ Service agents subset of cap_docker works correctly
- ✓ jellyfin correctly excluded from cap_service_agents

## Files Created/Modified

### Created (17 files)

```
docs/
  inventory-structure-guide.md
  inventory-visualization.md
  inventory-migration-guide.md

inventory/
  README.md
  group_vars/
    tier_tiny/vars.yml
    tier_small/vars.yml
    tier_medium/vars.yml
    tier_large/vars.yml
    cap_docker/vars.yml
    cap_gpu/vars.yml
    cap_wireguard/vars.yml
    cap_service_agents/vars.yml
  host_vars/
    codeserver.yml
    frontend.yml
    media.yml
    jellyfin.yml
```

### Modified (4 files)

```
inventory/
  hosts.yml
  group_vars/
    all/proxmox.yml
    proxmox_api/vars.yml
  host_vars/
    jellyfin_lxc.yml
```

## Key Design Decisions

### 1. Resource Tiers are Mutually Exclusive
Each host belongs to exactly ONE resource tier. This prevents conflicts in CPU/RAM/disk allocation.

### 2. Functional Groups are Compositional
Hosts can belong to MULTIPLE functional groups. This enables mixing capabilities (e.g., Docker + GPU).

### 3. Service Agents ⊂ Docker Hosts
The `cap_service_agents` group is designed as a subset of `cap_docker`. All service agents must be Docker hosts, but not all Docker hosts need to be service agents (e.g., jellyfin, future reverse-proxy node).

### 4. Variables Reference Groups
Host vars use `"{{ lxc_cores }}"` instead of hardcoded values. This makes overrides explicit and visible.

### 5. Exceptions via host_vars
Resource overrides are handled in host_vars with clear documentation of why the override is needed.

## Usage Examples

### View inventory structure
```bash
ansible-inventory --host media --yaml
```
ansible-playbook playbooks/lxc-provision.yml --limit tier_small
```

### Configure all Docker hosts
```bash
ansible-playbook playbooks/docker-setup.yml --limit cap_docker
```

### Configure service agents only
```bash
ansible-playbook playbooks/service-agents-setup.yml --limit cap_service_agents
```

## Benefits of New Structure

1. **Clear Resource Allocation:** Easy to see and modify resource specs by tier
2. **Flexible Capabilities:** Mix and match functional groups per host
3. **Scalable:** Easy to add new hosts by assigning to existing groups
4. **Maintainable:** Variables in logical, focused group files
5. **Documented:** Comprehensive guides for understanding and using the structure
6. **Validated:** All YAML syntax checked, variable resolution tested

## Next Steps

To use this structure:

1. **Review Documentation:** Read `docs/inventory-structure-guide.md`
2. **Understand Visualization:** Review `docs/inventory-visualization.md`
3. **Plan Migration:** If migrating, see `docs/inventory-migration-guide.md`
4. **Add New Hosts:** Follow patterns in existing host_vars files
5. **Customize:** Adjust resource tier specs if needed for your environment
6. **Integrate with Playbooks:** Update playbooks to target new groups

## Future Enhancements

Potential additions (not implemented, but structure supports):

- Additional resource tiers (e.g., xlarge for 16+ cores)
- More functional groups (e.g., monitoring_agents, backup_targets)
- Environment separation (production vs staging groups)
- Dynamic inventory integration with Proxmox API
- Automated host_vars generation tools

## Compatibility

- **Ansible Version:** Tested structure compatible with ansible-core 2.19 / Ansible 12
- **Backward Compatibility:** Legacy `lxcs` group maintained for existing playbooks
- **Migration Path:** Comprehensive migration guide provided for legacy structure

## Testing

To test this structure:

```bash
# 1. Validate YAML syntax
yamllint inventory/

# 2. Check inventory structure
ansible-inventory --graph

# 3. Verify variable inheritance for each host
for host in codeserver frontend media jellyfin; do
  echo "=== $host ==="
  ansible-inventory --host $host --yaml | head -50
done

# 4. Test with dry-run playbook
ansible-playbook playbooks/lxc-provision.yml --check --diff
```

## Support

For questions or issues:

1. Check documentation in `docs/` directory
2. Review examples in `inventory/host_vars/`
3. Validate with `ansible-inventory` commands
4. Create issue at: https://github.com/faviann/ServerManagementScripts/issues
