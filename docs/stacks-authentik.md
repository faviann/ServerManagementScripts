# Authentik Integration

Read when creating or modifying Authentik providers, applications, or auth bypass rules for a stack.

Traefik edge auth is explicit per protected router via `protected-edge-auth@file`.
Public routers should stay edge-open unless there is a specific reason to protect them at Traefik.

The shared proxy providers cover the protected tiers, so most protected services need no per-app Authentik object just to require login.

Create a dedicated Authentik provider + application when:
- access is group-restricted
- the hostname needs a non-catch-all provider
- the service is self-auth and should stay edge-open at Traefik

Use the provider type that matches the integration: Proxy Provider for Authentik forwardAuth/proxy flows, OAuth2/OpenID Provider for apps that do native OIDC.

Repo-wide rules:
- admin uses shared `admin-wildcard-forwardauth`
- home uses shared `home-wildcard-forwardauth`
- media uses shared `media-wildcard-forwardauth`
- root `faviann.com` uses `Provider for Domain Wide Forward Auth Catch All` when edge auth is desired
- shared callback tiers rely on global `AUTHENTIK_COOKIE_DOMAIN=.faviann.com`

Important: only protected routers should send traffic through Traefik forwardAuth. Public apps that use native auth or OIDC should not also sit behind Traefik forwardAuth.
For protected tiers, the matching provider still needs the outpost callback URL allowlisted in `Unauthenticated URLs / Paths`, including `https://<domain>/outpost.goauthentik.io/...`.

| Need | Action |
| --- | --- |
| Basic protected-tier login | add `protected-edge-auth@file`; shared provider handles login |
| Public app with native auth/OIDC | no Traefik auth middleware |
| Shared admin-tier login | keep `admin-wildcard-forwardauth` synced |
| Group restriction | create provider + application and bind groups |

Current shared protected-tier providers: `admin-wildcard-forwardauth`, `home-wildcard-forwardauth`, `media-wildcard-forwardauth`, and `Provider for Domain Wide Forward Auth Catch All`.

## Access Groups

Use domain bundle groups for ordinary application access:

| Group | Use for |
| --- | --- |
| `media` | Media applications and the media protected tier. |
| `reading` | Reading applications such as Audiobookshelf, Komga, Calibre-Web Automated, and ReadMeABook. |
| `storage` | Home-tier file, document, and storage services. |

`admins` is bound to every gated repo-managed application as the admin override
group. Per-app groups are created only when a user needs access to one
application without joining the wider domain bundle.

For app-specific Authentik objects, bind access at the application using this
convention:

| Order | Binding |
| --- | --- |
| `0` | Per-app group, only when one exists. |
| `1` | Domain bundle group. |
| `2` | `admins`. |

Keep `policy_engine_mode: any` on gated applications so any matching group
grants access.

## Native OIDC Pattern

For public apps that should stay edge-open and handle login themselves:

- do not add `protected-edge-auth@file` to the Traefik router
- create an Authentik OAuth2/OpenID provider plus application
- select an Authentik signing key explicitly in the provider so the JWKS endpoint publishes keys for the client
- add the app's OIDC environment variables via `.env.j2`
- keep redirect URIs exact for the app's callback path

Repo-managed native OIDC app definitions live with the Authentik stack:

- Manifest: `stacks/auth/auth/appdata/authentik/oidc-apps.yaml`
- Generated blueprint template: `stacks/auth/auth/appdata/authentik/blueprints/80-oidc-apps.yaml.j2`

Set `group:` in the manifest to the app's domain bundle, or to `admins` for an
intentionally admins-only application. The generated blueprint replaces
`always-allow` with domain-group and `admins` bindings. An admins-only app
therefore gets the same `admins` group at both orders; under
`policy_engine_mode: any` that is one redundant but harmless binding.

Set `grant_types:` on any app whose provider is created by this blueprint on
Authentik 2026.5 or later. New providers default to an empty grant list, and
both the authorize and token endpoints reject a grant that is not listed.
Providers that predate 2026.5 were backfilled by migration and omit the key, so
the manifest leaves their live grant sets untouched.

RomM is the current concrete native OIDC example. Its app-specific notes live in [stacks/public/romm/README.md](../stacks/public/romm/README.md).
