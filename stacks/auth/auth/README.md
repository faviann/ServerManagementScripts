# Authentik Stack

Authentik is a foundational identity stack on the `auth` Docker host. Its base
`compose.yaml` intentionally stays close to upstream Authentik shape, while
repo-owned behavior lives in the override layer.

## Normalization Boundary

This stack intentionally does not follow every ordinary app-stack default.

Preserve:

- Do not force full contract normalization into `compose.yaml`.
- Do not remove `env_file:` from the upstream-shaped base compose.
- Do not force `container_name` into upstream services.
- Do not convert or remove the base named volume solely for style; runtime
  database data is redirected to `./appdata/database` by the override.
- Do not move repo-specific override behavior into the base compose just to
  reduce files.

Do not use this stack as a template for normal application stacks.

## Ownership

Stack-owned:

- `compose.yaml`
- `compose.override.yaml.j2`
- `.env.j2`
- committed Authentik blueprints and custom templates under `appdata/`

Host-owned:

- auth vault-backed variable bindings in `inventory/host_vars/auth.yml`
- cross-host OIDC and automation inputs declared through host vars

## Managed Authentik Architecture

Repo-owned Authentik behavior is managed through blueprints under
`appdata/authentik/blueprints/`. The blueprints define groups, roles, flows,
providers, applications, service accounts, outposts, and native OIDC apps.

Core access groups:

| Group | Purpose |
| --- | --- |
| `admins` | Admin bundle. Bound to every gated repo-managed application. |
| `media` | Media bundle for media applications and forward-auth tiers. |
| `reading` | Reading bundle for Audiobookshelf, Komga, Calibre-Web Automated, ReadMeABook, and similar apps. |
| `storage` | Storage/files bundle for home-tier file and document services. |

Integration groups:

| Group | Purpose |
| --- | --- |
| `ldapsearch` | LDAP service account access. Not a human app bundle. |
| `PVEAdmins` | Proxmox OIDC group claim mapping. |

Per-app groups are created only when a user needs access to one application
without joining its domain bundle. Do not pre-create per-app groups speculatively.

Application bindings follow this order convention:

| Order | Binding |
| --- | --- |
| `0` | Per-app group, only when one exists. |
| `1` | Domain bundle group such as `media`, `reading`, or `storage`. |
| `2` | `admins`, always present on gated repo-managed apps. |

Applications use `policy_engine_mode: any`, so any matching binding grants
access. Stale order-0 `always-allow` bindings are removed with absent
tombstones before group bindings are applied.

`15-roles.yaml` creates the `registration-approver` Authentik role with
`authentik_core.view_user` and `authentik_core.change_user`. The role is
intentionally unassigned by default; assign it separately only when there is a
specific sub-admin user.

`90-cleanup-legacy.yaml` removes the old `content-editors` group after
application bindings no longer reference it.

## Deploy

```bash
./run.sh --limit auth -e stack_filter=auth
```
