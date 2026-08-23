# SABnzbd Stack

SABnzbd is a host-bound download stack on the `seedbox` Docker host. It does not use the Gluetun VPN namespace; the web UI port is published directly from the `sabnzbd` service.

## Ownership

Stack-owned:

- `compose.yaml`
- `.env.j2`
- this `README.md`
- `./appdata/sabnzbd`
- `/ephemeral/sabnzbd` prereq declaration

Host-owned:

- `default_domain`
- `/data` and `/ephemeral` mounts
- host-level LXC and Docker settings in `inventory/host_vars/seedbox.yml`

## Deploy

```bash
./run.sh --limit seedbox -e stack_filter=sabnzbd
```
