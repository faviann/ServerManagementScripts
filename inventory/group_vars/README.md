# group_vars layout

This directory organizes inventory variables by purpose:

- `all/` — shared defaults (Proxmox API credentials, host prep settings, etc.)
- `tier_*` — resource tiers describing baseline CPU/RAM/disk for LXCs
- `cap_*` — capability overlays layered on top of tiers (Docker, GPU, WireGuard)
- `proxmox_api/` — variables for the controller host that drives API calls

Each group directory contains a `vars.yml`, so the directory name matches the
Ansible group it configures. Add `files/` or `vault/` subfolders alongside
`vars.yml` if a group later needs templates or vaulted data.
