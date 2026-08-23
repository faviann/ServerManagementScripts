# RomM Stack

RomM is a host-bound public app stack on the `public` Docker host. It stays edge-open at Traefik and uses native OIDC through Authentik.

## OIDC Ownership

Stack-owned:

- `compose.yaml`
- `.env.j2`
- this `README.md`
- RomM runtime OIDC environment variables rendered from `.env.j2`

Auth-owned:

- OIDC app manifest: `stacks/auth/auth/appdata/authentik/oidc-apps.yaml`
- OIDC blueprint template: `stacks/auth/auth/appdata/authentik/blueprints/80-oidc-apps.yaml.j2`
- Authentik signing key selection through `auth_romm_oidc_signing_certificate_name` in `inventory/host_vars/auth.yml`

Host-owned:

- `default_domain`
- `/data/roms` library mount
- `public_romm_*` vault-backed variable bindings in `inventory/host_vars/public.yml`

## OIDC Notes

- Authentik 2025.10+ defaults `email_verified` to `false`, so RomM needs a scope mapping that returns `email_verified: True`.
- `OIDC_SERVER_APPLICATION_URL` should use the Authentik application URL with its trailing slash intact.
- A user's email in RomM must match the user's email in Authentik.
- First-time OIDC users are created in RomM with viewer permissions.
- If the provider stays on Authentik's symmetric default signing mode, metadata may advertise `HS256` and the JWKS endpoint may return `{}`, which breaks RomM OIDC login.
- The certificate/keypair referenced by `auth_romm_oidc_signing_certificate_name` must exist in Authentik.

## Deploy

```bash
./run.sh --limit public -e stack_filter=romm
```
