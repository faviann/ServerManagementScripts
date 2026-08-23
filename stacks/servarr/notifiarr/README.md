# Notifiarr Stack

Notifiarr is a portable app stack on the `servarr` Docker host. It exposes the web UI through Traefik with `protected-edge-auth@file` and publishes an admin Homepage card.

## Ownership

Stack-owned:

- `compose.yaml`
- `.env.j2`
- this `README.md`
- `stack.yaml`
- `./appdata/notifiarr`

Host-owned:

- `default_domain`
- `shared` external Docker network declaration
- `/data` host directory contract
- LXC identity and resource settings

## Deploy

```bash
./run.sh --limit servarr -e stack_filter=notifiarr
```

Expected result: Ansible renders `.env.j2`, copies deployable stack files only, leaves this README and `stack.yaml` out of `/conf/docker/stacks/notifiarr`, and runs `docker compose up -d` for `notifiarr`.
