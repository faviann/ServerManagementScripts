# ReadMeABook Stack

ReadMeABook is a host-bound public app stack on the `public` Docker host. It provides audiobook request and automation via published port, routed through Traefik.

## Ownership

Stack-owned:

- `compose.yaml`
- `.env.j2`
- this `README.md`

Host-owned:

- `default_domain`
- `/data/downloads/readmeabook` download mount
- `/data/media/audiobooks` media mount
- `public_readmeabook_*` vault-backed variable bindings in `inventory/host_vars/public.yml`

## Deploy

```bash
./run.sh --limit public -e stack_filter=readmeabook
```
