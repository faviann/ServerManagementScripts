# Traefik3 Stack

This stack is the domain-edge reverse proxy on the `portal` Docker host. It is
infrastructure, not a normal routed application stack.

## Normalization Boundary

This stack intentionally does not follow every ordinary app-stack default.

Preserve:

- Do not remove either `443/tcp` or `443/udp`; same-number TCP and UDP bindings
  are not a port conflict.
- Keep Docker provider socket access behind the stack-local
  `traefik-docker-socket-proxy`; Traefik should not mount the host Docker socket
  directly.
- Do not treat Redis, certificate storage, or `x-managed-files` as
  exception-only behavior. They are normal features for this domain-edge reverse
  proxy pattern.
- Do not read or print files under `stacks/portal/traefik3/secrets/`.
- Do not document `.env` values.
- Do not normalize this stack as if it were a normal routed application.

Do not use this stack as a template for normal application stacks.

## Ownership

Stack-owned:

- `compose.yaml`
- `.env.j2`
- Traefik dynamic and static config under `appdata/`
- ACME storage declarations through `x-managed-files`

Host-owned:

- `shared` external network declaration
- portal vault-backed variable bindings in `inventory/host_vars/portal.yml`
- domain-edge exposure and certificate DNS credentials

## Deploy

```bash
./run.sh --limit portal -e stack_filter=traefik3
```
