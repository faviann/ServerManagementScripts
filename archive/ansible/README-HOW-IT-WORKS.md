# Ansible LXC Container Management - How It Works

This document provides a deep dive into the architecture, design decisions, and technical implementation of the Ansible-based LXC container management system.

## Table of Contents
- [Architecture Overview](#architecture-overview)
- [Directory Structure](#directory-structure)
- [Role Descriptions](#role-descriptions)
- [Execution Flow](#execution-flow)
- [Idempotency Implementation](#idempotency-implementation)
- [Configuration Management](#configuration-management)
- [Technical Details](#technical-details)
- [Extending the System](#extending-the-system)

## Architecture Overview

This automation system follows Ansible best practices and is designed to be:
- **Idempotent**: Safe to run multiple times
- **Modular**: Separate roles for different concerns
- **Declarative**: Describe desired state, not steps
- **Version-controlled**: All configuration in YAML files
- **Local-first**: Runs on Proxmox host itself

### Design Principles

1. **Separation of Concerns**: Each role handles one aspect (creation, configuration, GPU, etc.)
2. **Configuration as Code**: All container specifications in version-controlled YAML
3. **Fail-Safe**: Checks before every destructive operation
4. **Observable**: Clear output messages and logging
5. **Extensible**: Easy to add new roles or modify existing ones

### Component Diagram

```
┌─────────────────────────────────────────────────────┐
│                  Ansible Control                     │
│                  (Proxmox Host)                      │
└─────────────────────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  Inventory   │ │  Group Vars  │ │  Host Vars   │
│  hosts.yml   │ │   all.yml    │ │ {name}.yml   │
└──────────────┘ └──────────────┘ └──────────────┘
        │               │               │
        └───────────────┼───────────────┘
                        │
                        ▼
        ┌───────────────────────────────┐
        │        Playbooks              │
        │  ┌─────────────────────────┐  │
        │  │  create-lxc.yml         │  │
        │  │  configure-lxc.yml      │  │
        │  │  update-all.yml         │  │
        │  └─────────────────────────┘  │
        └───────────────────────────────┘
                        │
        ┌───────────────┼───────────────┬───────────────┬───────────────┐
        ▼               ▼               ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ lxc_create   │ │lxc_configure │ │  lxc_nvidia  │ │lxc_wireguard │ │  lxc_dockge  │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
        │               │               │               │               │
        └───────────────┴───────────────┴───────────────┴───────────────┘
                                        │
                                        ▼
                        ┌───────────────────────────────┐
                        │     Proxmox pct commands      │
                        │   LXC Container Management    │
                        └───────────────────────────────┘
                                        │
                                        ▼
                        ┌───────────────────────────────┐
                        │      LXC Containers           │
                        │  ┌────────┐  ┌────────┐       │
                        │  │  CT1   │  │  CT2   │  ...  │
                        │  └────────┘  └────────┘       │
                        └───────────────────────────────┘
```

## Directory Structure

```
ansible/
├── ansible.cfg                    # Ansible configuration
├── inventory/
│   └── hosts.yml                  # Inventory of all containers
├── host_vars/                     # Per-container configuration
│   ├── example-app.yml            # Example without GPU
│   └── gpu-app.yml                # Example with GPU
├── group_vars/
│   └── all.yml                    # Default values for all containers
├── roles/
│   ├── lxc_create/                # Container creation role
│   │   ├── tasks/
│   │   │   └── main.yml           # Creation tasks
│   │   ├── defaults/
│   │   │   └── main.yml           # Default variables
│   │   └── templates/             # (reserved for future use)
│   ├── lxc_configure/             # Basic configuration role
│   │   ├── tasks/
│   │   │   └── main.yml           # UID/GID mapping tasks
│   │   ├── handlers/
│   │   │   └── main.yml           # Restart handlers
│   │   ├── templates/             # (reserved for future use)
│   │   └── defaults/
│   │       └── main.yml           # Default UID/GID mappings
│   ├── lxc_nvidia/                # NVIDIA GPU passthrough role
│   │   ├── tasks/
│   │   │   └── main.yml           # GPU configuration tasks
│   │   └── defaults/
│   │       └── main.yml           # NVIDIA configuration lines
│   ├── lxc_wireguard/             # VPN/tunnel support role
│   │   └── tasks/
│   │       └── main.yml           # Wireguard tun device setup
│   └── lxc_dockge/                # Docker & Dockge setup role
│       ├── tasks/
│       │   └── main.yml           # Inside-container configuration
│       ├── templates/
│       │   ├── journald.conf.j2   # Journald log limits
│       │   └── fstab-docker.j2    # Docker mount entry
│       └── defaults/
│           └── main.yml           # Docker user and paths
├── playbooks/
│   ├── create-lxc.yml             # Create new container
│   ├── configure-lxc.yml          # Reconfigure existing container
│   └── update-all.yml             # Update all containers
├── README-USAGE.md                # User guide (this file's companion)
└── README-HOW-IT-WORKS.md         # Technical documentation (this file)
```

## Role Descriptions

### Role: lxc_create

**Purpose**: Create LXC container and configure mount points

**Responsibilities**:
- Check if container already exists (idempotency)
- Download Debian 12 template if not cached
- Create unprivileged LXC container with specified resources
- Configure network interface on vmbr1 with DHCP
- Add 4 mount points (mp0-mp3)
- Extract MAC address for network configuration

**Key Variables**:
- `lxc_id`: Container ID (required)
- `container_name`: Hostname (required)
- `disk_size`, `ram_size`, `core_count`: Resources
- `mp0_*`, `mp1_*`, etc.: Mount point configurations

**Idempotency Strategy**:
- Checks container existence before creation
- Checks each mount point before adding
- Uses `pct` commands which are inherently idempotent for updates

**Why `shell` module?**:
- `pct` commands don't have native Ansible modules
- Mount point configuration requires `pct set` with specific syntax
- We ensure idempotency by checking before executing

### Role: lxc_configure

**Purpose**: Configure UID/GID mappings for proper file permissions

**Responsibilities**:
- Add UID/GID mappings to LXC config file
- Restart container to apply mappings
- Wait for container to be ready

**Key Variables**:
- `uid_gid_mappings`: List of mapping entries

**UID/GID Mapping Explanation**:
```
lxc.idmap: u 0 100000 1000      # Map container UID 0-999 to host 100000-100999
lxc.idmap: g 0 100000 1000      # Map container GID 0-999 to host 100000-100999
lxc.idmap: u 1000 1001 1        # Map container UID 1000 to host UID 1001
lxc.idmap: g 1000 1001 1        # Map container GID 1000 to host GID 1001
lxc.idmap: u 1001 101001 64535  # Map container UID 1001+ to host 101001+
lxc.idmap: g 1001 101001 64535  # Map container GID 1001+ to host 101001+
```

This ensures:
- Container user with UID 1000 (dockeruser) has UID 1001 on host
- Files created by dockeruser are owned by host UID 1001
- Bind mounts work correctly with /tank and /shared

**Idempotency Strategy**:
- Uses `blockinfile` with markers to prevent duplicate entries
- Checks if mappings already exist before adding

### Role: lxc_nvidia

**Purpose**: Enable NVIDIA GPU passthrough in container

**Responsibilities**:
- Add NVIDIA-specific LXC configuration
- Configure environment variables for GPU visibility
- Set up pre-start hooks for nvidia-modprobe

**Key Variables**:
- `enable_nvidia`: Boolean flag to enable/disable
- `nvidia_lxc_configs`: List of configuration lines

**Configuration Added**:
```
lxc.hook.pre-start: sh -c '[ ! -f /dev/nvidia0 ] && /usr/bin/nvidia-modprobe -c0 -u'
lxc.environment: NVIDIA_VISIBLE_DEVICES=all
lxc.environment: NVIDIA_DRIVER_CAPABILITIES=compute,utility,video
lxc.hook.mount: /usr/share/lxc/hooks/nvidia
```

**Idempotency Strategy**:
- Checks if NVIDIA config already exists
- Only adds configuration if not present
- Conditional execution based on `enable_nvidia` flag

**Note**: Requires manual post-configuration step to edit `/etc/nvidia-container-runtime/config.toml`

### Role: lxc_wireguard

**Purpose**: Enable Wireguard/VPN tunneling support

**Responsibilities**:
- Mount /dev/net/tun device into container
- Restart container to apply changes

**Key Variables**:
- `enable_wireguard`: Boolean flag to enable/disable

**Configuration Added**:
```
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
```

**Idempotency Strategy**:
- Uses `lineinfile` to prevent duplicate entries
- Checks if tun device mount already exists
- Conditional execution based on `enable_wireguard` flag

### Role: lxc_dockge

**Purpose**: Configure Docker, Dockge, and inside-container setup

**Responsibilities**:
1. Check and copy Dockge template from `.template` folder
2. Update system packages
3. Create bind mount for Docker configuration
4. Create `dockeruser` (UID 1000)
5. Install and configure NVIDIA Container Toolkit (if GPU enabled)
6. Start Dockge
7. Configure journald log limits
8. Reboot container

**Key Variables**:
- `docker_user`, `docker_uid`, `docker_gid`: Docker user settings
- `zfs_volume_path`: Path to ZFS volume
- `template_source`: Path to template folder
- `journald_max_use`, `journald_max_file_size`: Log limits
- `dockge_path`: Path to Dockge installation

**Inside-Container Operations**:
All operations use `pct exec` to run commands inside the container:

```bash
pct exec {lxc_id} -- bash -c "command"
```

**Template Copy Logic**:
```
1. Check if ZFS volume is mounted
2. Check if container subfolder exists
3. If not, copy .template folder
4. Set ownership to 1001:1001
```

**Docker User Setup**:
```
1. Check if dockeruser exists
2. Create with no password
3. Add to docker and sudo groups
```

**NVIDIA Container Toolkit** (if enabled):
```
1. Install gpg
2. Add NVIDIA GPG key
3. Add NVIDIA repository
4. Update apt cache
5. Install nvidia-container-toolkit
6. Configure for Docker runtime
7. Display manual configuration step
```

**Idempotency Strategy**:
- Checks before every operation (user exists, package installed, etc.)
- Uses conditional `when` clauses
- Template copy only if destination doesn't exist
- Package installation only if not already installed

## Execution Flow

### Create New Container Flow

```
START
  │
  ├─> Validate variables (lxc_id, container_name)
  │
  ├─> [lxc_create role]
  │   ├─> Check if container exists
  │   ├─> Download template (if needed)
  │   ├─> Create container (if doesn't exist)
  │   ├─> Configure network
  │   ├─> Add mount points (mp0-mp3)
  │   └─> Extract MAC address
  │
  ├─> [lxc_configure role]
  │   ├─> Add UID/GID mappings
  │   ├─> Stop container
  │   ├─> Start container
  │   └─> Wait for ready
  │
  ├─> [lxc_nvidia role] (if enable_nvidia=true)
  │   ├─> Check if NVIDIA config exists
  │   ├─> Add NVIDIA configuration
  │   ├─> Restart container
  │   └─> Wait for ready
  │
  ├─> [lxc_wireguard role] (if enable_wireguard=true)
  │   ├─> Check if tun device exists
  │   ├─> Add tun device mount
  │   ├─> Restart container
  │   └─> Wait for ready
  │
  ├─> [lxc_dockge role]
  │   ├─> Check ZFS volume mounted
  │   ├─> Copy template (if needed)
  │   ├─> Update packages inside container
  │   ├─> Create bind mount
  │   ├─> Add to fstab
  │   ├─> Create dockeruser
  │   ├─> Install NVIDIA toolkit (if GPU enabled)
  │   ├─> Start Dockge
  │   ├─> Configure journald
  │   └─> Reboot container
  │
  └─> Display completion message
END
```

### Reconfigure Existing Container Flow

```
START
  │
  ├─> Validate variables
  ├─> Check container exists (fail if not)
  │
  ├─> [lxc_configure role]
  │   └─> Re-apply UID/GID mappings (idempotent)
  │
  ├─> [lxc_nvidia role] (if enabled)
  │   └─> Re-apply NVIDIA config (idempotent)
  │
  ├─> [lxc_wireguard role] (if enabled)
  │   └─> Re-apply Wireguard config (idempotent)
  │
  ├─> [lxc_dockge role]
  │   └─> Re-run all configuration (idempotent)
  │
  └─> Display completion message
END
```

### Update All Containers Flow

```
START
  │
  ├─> For each container in lxc_containers (serial=1):
  │   ├─> Check container exists (skip if not)
  │   ├─> Ensure container is running
  │   ├─> Update apt cache
  │   ├─> Upgrade packages
  │   ├─> Check if Dockge is running
  │   ├─> Restart Dockge (if was running)
  │   └─> Next container
  │
  └─> Display summary
END
```

## Idempotency Implementation

Idempotency means the playbook can be run multiple times without causing unintended changes. We achieve this through:

### 1. Check-Before-Execute Pattern

Every potentially changing operation checks if the change is needed:

```yaml
- name: Check if container exists
  ansible.builtin.shell: pct status {{ lxc_id }} || echo "not_found"
  register: container_status
  changed_when: false

- name: Create container (only if doesn't exist)
  ansible.builtin.shell: pct create ...
  when: "'not_found' in container_status.stdout"
```

### 2. Using Idempotent Modules

Prefer Ansible modules over shell commands when possible:

```yaml
# Idempotent - uses blockinfile with markers
- name: Add UID/GID mappings
  ansible.builtin.blockinfile:
    path: "{{ container_conf }}"
    marker: "# {mark} ANSIBLE MANAGED UID/GID MAPPINGS"
    block: |
      {% for mapping in uid_gid_mappings %}
      lxc.idmap: {{ mapping }}
      {% endfor %}
```

### 3. Conditional Execution

Use `when` clauses to skip unnecessary operations:

```yaml
- name: Install NVIDIA toolkit
  ansible.builtin.shell: ...
  when: 
    - enable_nvidia | bool
    - "'not_found' in nvidia_toolkit_check.stdout"
```

### 4. Template Copy Safety

```yaml
- name: Check if container subfolder exists
  ansible.builtin.stat:
    path: "{{ zfs_volume_path }}/{{ container_name }}"
  register: container_subfolder

- name: Copy template (only if doesn't exist)
  ansible.builtin.copy:
    src: "{{ template_source }}/"
    dest: "{{ zfs_volume_path }}/{{ container_name }}/"
  when: not container_subfolder.stat.exists
```

### 5. Changed Reporting

Properly report when changes are made:

```yaml
- name: Update packages
  ansible.builtin.shell: apt upgrade -y
  register: apt_upgrade
  changed_when: "'0 upgraded' not in apt_upgrade.stdout"
```

### 6. No-Op on Existing Configuration

Using `blockinfile` and `lineinfile` with proper markers ensures:
- First run: Adds configuration
- Second run: No changes (block already exists)
- Third run: No changes (still exists)

## Configuration Management

### Variable Precedence

Ansible uses this precedence (highest to lowest):

1. **Extra vars** (`-e` on command line)
2. **host_vars/** (per-container)
3. **group_vars/** (shared defaults)
4. **Role defaults/** (role-specific defaults)

Example:
```yaml
# group_vars/all.yml (default for all)
disk_size: 8

# host_vars/gpu-app.yml (override for specific container)
disk_size: 16

# Command line (highest precedence)
ansible-playbook ... -e "disk_size=32"
```

### Inventory Structure

```yaml
all:
  children:
    proxmox_host:      # The Proxmox host itself
      hosts:
        localhost:     # We run playbooks locally
    
    lxc_containers:    # Group for all containers
      hosts:
        example-app:   # Individual container
          lxc_id: 201
        gpu-app:
          lxc_id: 202
```

### Configuration Files

**ansible.cfg**:
- Sets defaults for all playbook runs
- Configures connection type (local)
- Sets privilege escalation (become root)
- Configures logging and output

**group_vars/all.yml**:
- Default values for all containers
- Mount point configurations
- UID/GID mappings
- Template paths

**host_vars/{name}.yml**:
- Per-container overrides
- Container-specific resources
- Feature flags (GPU, Wireguard)

## Technical Details

### UID/GID Mapping Deep Dive

**Why do we need this?**

LXC containers are unprivileged by default. This means:
- Container's root (UID 0) maps to host UID 100000
- Container's user 1000 maps to host UID 101000
- Files created in bind mounts have "wrong" ownership

**Our mapping strategy**:

```
Container UID  →  Host UID      Purpose
─────────────────────────────────────────
0-999          →  100000-100999  System users
1000           →  1001           dockeruser → host UID 1001
1001+          →  101001+        Other users
```

This allows:
- `dockeruser` (UID 1000 in container) creates files as UID 1001 on host
- `/tank` is owned by UID 1001 on host
- Files are properly accessible

### Mount Points Explained

**mp0: ZFS Subvolume** (`/shared`)
- Storage: `local-zfs:subvol-200-disk-1`
- Size: 256GB
- Purpose: Shared ZFS volume for all containers
- Each container gets subfolder: `/shared/{container_name}`

**mp1: Configuration** (`/conf`)
- Source: `/conf` on host
- Read-only: Yes
- Purpose: Shared configuration directory

**mp2: Ephemeral** (`/ephemeral`)
- Source: `/ephemeral` on host
- Purpose: Temporary/ephemeral storage

**mp3: Data** (`/data`)
- Source: `/tank` on host
- Purpose: Main data storage (NAS/bulk storage)

### Network Configuration

Containers use:
- **Bridge**: vmbr1 (not default vmbr0)
- **IP Assignment**: DHCP (automatic)
- **IPv6**: Auto-configuration enabled
- **MAC Address**: Automatically generated, extracted for reference

### Docker User Setup

The `dockeruser`:
- **UID**: 1000 (in container)
- **Host UID**: 1001 (via UID mapping)
- **No Password**: Can't login via SSH
- **Groups**: docker, sudo
- **Purpose**: Run Docker containers without root

### Journald Configuration

Limits container log disk usage:
```
SystemMaxUse=1000M        # Maximum 1GB total logs
SystemMaxFileSize=20M     # 20MB per log file
```

This prevents Docker containers from filling disk with logs.

### NVIDIA GPU Passthrough

**Host Requirements**:
- NVIDIA drivers installed
- nvidia-modprobe available
- LXC hooks script at `/usr/share/lxc/hooks/nvidia`

**Container Configuration**:
1. Pre-start hook runs nvidia-modprobe
2. Environment variables expose GPU
3. Mount hook mounts GPU devices
4. nvidia-container-toolkit configures Docker

**Manual Step Required**:
Edit `/etc/nvidia-container-runtime/config.toml`:
```toml
[nvidia-container-cli]
no-cgroups = true  # Change from false to true
```

This is because unprivileged containers can't use cgroups v1.

### Dockge Template

Expected structure:
```
/rpool/data/subvol-200-disk-1/
└── .template/
    └── dockge/
        ├── docker-compose.yml
        ├── data/
        └── stacks/
```

Copied to:
```
/rpool/data/subvol-200-disk-1/{container_name}/
└── dockge/
    ├── docker-compose.yml
    ├── data/
    └── stacks/
```

Then bind-mounted:
```
/shared/{container_name} → /conf/docker (in container)
```

So Dockge runs from `/conf/docker/dockge` inside container.

## Extending the System

### Adding a New Role

1. **Create role directory structure**:
```bash
mkdir -p roles/my_feature/{tasks,defaults,templates,handlers}
```

2. **Create tasks file**:
```yaml
# roles/my_feature/tasks/main.yml
---
- name: Check if feature exists
  ansible.builtin.shell: ...
  register: feature_check
  changed_when: false

- name: Configure feature
  ansible.builtin.shell: ...
  when: "'not_found' in feature_check.stdout"
```

3. **Add defaults**:
```yaml
# roles/my_feature/defaults/main.yml
---
enable_my_feature: false
my_feature_setting: "value"
```

4. **Include in playbook**:
```yaml
# playbooks/create-lxc.yml
  roles:
    # ... existing roles ...
    - role: my_feature
      when: enable_my_feature | bool
      tags: ['my_feature', 'setup']
```

### Adding New Container Variables

1. **Add to group_vars** (for all containers):
```yaml
# group_vars/all.yml
my_new_setting: "default_value"
```

2. **Override in host_vars** (for specific container):
```yaml
# host_vars/special-container.yml
my_new_setting: "custom_value"
```

3. **Use in role**:
```yaml
# roles/some_role/tasks/main.yml
- name: Apply my setting
  ansible.builtin.shell: echo "{{ my_new_setting }}"
```

### Adding Custom Mount Points

1. **Define in group_vars or host_vars**:
```yaml
mp4_host_path: "/mnt/custom"
mp4_mountpoint: "/custom"
```

2. **Add to lxc_create role**:
```yaml
- name: Check if mp4 is configured
  ansible.builtin.shell: grep -q "^mp4:" {{ container_conf }}
  register: mp4_check
  changed_when: false

- name: Configure mp4
  ansible.builtin.shell: pct set {{ lxc_id }} --mp4 {{ mp4_host_path }},mp={{ mp4_mountpoint }}
  when: "'not_found' in mp4_check.stdout"
```

### Adding Tags for Selective Execution

Tags allow running specific parts of playbooks:

```yaml
# In playbook or role tasks
- name: Some task
  ansible.builtin.shell: ...
  tags: ['mytag', 'setup']
```

Run only tagged tasks:
```bash
ansible-playbook playbooks/create-lxc.yml -e "target_host=my-app" --tags mytag
```

Skip tagged tasks:
```bash
ansible-playbook playbooks/create-lxc.yml -e "target_host=my-app" --skip-tags mytag
```

### Creating Custom Playbooks

Example: Backup playbook

```yaml
---
# playbooks/backup-containers.yml
- name: Backup all LXC containers
  hosts: lxc_containers
  gather_facts: false
  become: true

  tasks:
    - name: Stop container
      ansible.builtin.shell: pct stop {{ lxc_id }}

    - name: Create backup
      ansible.builtin.shell: vzdump {{ lxc_id }} --storage backup

    - name: Start container
      ansible.builtin.shell: pct start {{ lxc_id }}
```

### Modifying Templates

Templates use Jinja2 syntax:

```jinja2
{# roles/lxc_dockge/templates/journald.conf.j2 #}
[Journal]
SystemMaxUse={{ journald_max_use }}
SystemMaxFileSize={{ journald_max_file_size }}
{% if enable_compression %}
Compress=yes
{% endif %}
```

Deploy with:
```yaml
- name: Configure journald
  ansible.builtin.template:
    src: journald.conf.j2
    dest: /etc/systemd/journald.conf
```

### Adding Pre/Post Tasks

In playbooks:

```yaml
- name: Create container
  hosts: "{{ target_host }}"
  
  pre_tasks:
    - name: Validate environment
      ansible.builtin.shell: ...
  
  roles:
    - lxc_create
  
  post_tasks:
    - name: Send notification
      ansible.builtin.shell: ...
```

### Creating Handlers

For tasks that should run once after changes:

```yaml
# roles/my_role/handlers/main.yml
---
- name: Restart service
  ansible.builtin.shell: systemctl restart myservice
  listen: "restart my service"
```

Trigger from tasks:
```yaml
- name: Configure service
  ansible.builtin.template:
    src: config.j2
    dest: /etc/myservice/config
  notify: "restart my service"
```

## Best Practices for Development

1. **Test changes in a dev environment first**
2. **Use `--check` mode for dry runs**:
   ```bash
   ansible-playbook playbooks/create-lxc.yml --check
   ```
3. **Use `-vvv` for debugging**
4. **Always implement idempotency checks**
5. **Document complex logic with comments**
6. **Use meaningful variable names**
7. **Group related tasks with comments**
8. **Test with existing containers to verify idempotency**
9. **Keep roles focused on single responsibility**
10. **Version control all changes**

## Performance Considerations

1. **Serial execution for updates**: `serial: 1` prevents resource exhaustion
2. **Fact gathering disabled**: We don't need Ansible facts for most operations
3. **Connection caching**: Uses control path for faster connections
4. **Local execution**: No SSH overhead (runs on Proxmox host)
5. **Conditional role execution**: Roles only run when needed

## Security Considerations

1. **Unprivileged containers**: All containers run unprivileged
2. **UID/GID mapping**: Prevents container breakout via file permissions
3. **Read-only mounts**: `/conf` mounted read-only
4. **No root SSH**: dockeruser has no password
5. **Sudo group**: dockeruser can sudo (for Docker operations only)
6. **GPU isolation**: NVIDIA hook isolates GPU access

## Troubleshooting Development

### Task Not Idempotent

Check:
1. Is there a check before execution?
2. Is `changed_when` set correctly?
3. Is the module idempotent (prefer modules over shell)?

### Variable Not Available

Check precedence:
1. Is it in host_vars?
2. Is it in group_vars?
3. Is it in role defaults?
4. Is it passed via `-e`?

Debug with:
```yaml
- name: Debug variable
  ansible.builtin.debug:
    var: my_variable
```

### Role Not Executing

Check:
1. Is it included in the playbook?
2. Are `when` conditions met?
3. Is it being skipped by tags?
4. Is the target host correct?

### Container Changes Not Applying

Some changes require container restart:
- UID/GID mappings
- Mount points
- NVIDIA configuration
- Wireguard configuration

Always stop and start (not restart) for LXC configuration changes.

## Conclusion

This system provides a robust, maintainable, and extensible way to manage Proxmox LXC containers. By following Ansible best practices and maintaining idempotency, it ensures safe, repeatable deployments that can be version-controlled and automated.

For usage instructions, see [README-USAGE.md](README-USAGE.md).
