# Music Stack

This stack runs Navidrome on the `public` Docker host with a split auth model.
`music.public.faviann.com` stays edge-open for Navidrome native auth and
Subsonic clients. `music.media.faviann.com` uses `protected-edge-auth@file` for
browser access.

ADR-005 documents the Authentik/Navidrome sync decision:
[`docs/decisions/adr-005-navidrome-authentik-sync.md`](../../../docs/decisions/adr-005-navidrome-authentik-sync.md).

## Normalization Boundary

This stack intentionally does not follow every ordinary app-stack default.

Preserve:

- Do not collapse the public and protected Traefik routers into one route.
- Do not add forwardAuth to the public music router.
- Do not remove the protected media router.
- Do not force `container_name: reconciler` solely for style;
  `navidrome-reconciler` is operationally clearer.
- Do not change reconciler secret flow or Authentik/Navidrome integration during
  stack style cleanup.

Do not use this stack as a template for normal application stacks.

## Ownership

Stack-owned:

- `compose.yaml`
- `.env.j2`
- `reconciler/`
- Navidrome routing and reconciler wiring

Auth-owned:

- Authentik policies, flows, and OIDC/provider behavior that feed Navidrome
  account lifecycle sync

Host-owned:

- music library path
- public host vault-backed variable bindings in `inventory/host_vars/public.yml`

## Deploy

```bash
./run.sh --limit public -e stack_filter=music
```
