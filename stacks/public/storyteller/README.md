# Storyteller Stack

Storyteller is a host-bound public app stack on the `public` Docker host. It exposes an audiobook and ebook platform via published port, routed through Traefik.

## Ownership

Stack-owned:

- `compose.yaml`
- `.env.j2`
- this `README.md`

Host-owned:

- `default_domain`
- `public_storyteller_secret_key` vault-backed variable binding in `inventory/host_vars/public.yml`

## Deploy

```bash
./run.sh --limit public -e stack_filter=storyteller
```
