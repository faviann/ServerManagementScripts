# Current Stack Update Surfaces

This inventory records the update-relevant shape of repo-managed stacks as of
2026-07-26. It describes current facts and ambiguities; it does not assign the
future update policies.

## Aggregate shape

- `stacks/` contains 37 stacks across 7 hosts and 51 effective
  service-image references after Compose overrides are applied.
- Only `overmind/postgres` and `servarr/notifiarr` have `stack.yaml`.
  Neither metadata file records an update authority, channel, or procedure.
- Four stacks have an override layer: `auth/auth`, `public/immich`,
  `public/readmeabook`, and `servarr/beets-flask`.
- Eight stacks have more than one effective image-bearing service:
  `auth/auth`, `jellyfin/jellystat`, `portal/traefik3`, `public/immich`,
  `public/music`, `public/romm`, `seedbox/bittorrent`, and
  `servarr/prowlarr`.
- No Renovate or Dependabot configuration currently exists in the repository.

The effective image references break down as follows:

| Reference shape | Service-image references |
| --- | ---: |
| Exact version-like tag | 23 |
| Floating `latest` | 8 |
| Untagged, and therefore implicitly `latest` | 5 |
| Variable with a default reference | 4 |
| Minor-version track | 4 |
| Major-version track | 2 |
| Digest-pinned | 2 |
| Named channel or variant | 2 |
| Floating `stable` | 1 |

These are service-reference counts, not unique repositories. Authentik uses the
same application image for two services, and several stacks share an image
repository.

## Compose ownership evidence

The repository currently provides evidence for four different situations:

1. **Explicit vendor tracking**: `auth/auth` says its base deliberately follows
   upstream Authentik shape and keeps repo-owned behavior in
   `compose.override.yaml.j2`.
2. **Embedded official vendor baseline**: `public/immich/compose.yaml` contains
   the official warning and current-release Compose URL; its override carries
   GPU and homelab adaptations.
3. **Vendor-preserving history or shape without complete current metadata**:
   `public/readmeabook` has a vendor-shaped base and local override, and its
   history names the conversion to vendor-preserving mode.
   `servarr/beets-flask` likewise separates a placeholder-filled upstream-shaped
   base from repo-owned mounts, routing, and managed-file behavior, but its
   README does not name an upstream source.
4. **Single repo-owned Compose file**: the remaining 33 stacks do not carry a
   machine-readable declaration distinguishing intentionally image-tracked
   ownership from an unrecorded vendor source.

An override file alone is therefore evidence of separation, but it is not a
sufficient machine-readable update policy. Later policy work must record the
upstream authority and tracking mode explicitly rather than infer them from the
file layout.

## Full stack inventory

“Single” means one repo-owned Compose layer with no declared upstream ownership.
It does not assert that the file was never derived from a vendor example.

| Stack | Current surface | Effective image references | Update-relevant constraints |
| --- | --- | --- | --- |
| `auth/auth` | Explicit vendor-tracked base + templated override | Authentik default `2026.2.3` on server and worker; Postgres `16-alpine` | Foundational identity stack; app and database are coupled; official upgrade guidance and local blueprint/OIDC behavior require manual assistance. |
| `jellyfin/jellyfin` | Single | Jellyfin `10.11.8` | Host-bound media stack with GPU configuration and shared media storage. |
| `jellyfin/jellystat` | Single | Jellystat `1.1.9`; Postgres `15.2` | Application and stateful database must be reviewed together. |
| `jellyfin/seerr` | Single | Seerr `v3.2.0` | Ordinary exact-version application reference. |
| `overmind/postgres` | Single + metadata | Postgres major track `18` | Dedicated stateful database substrate; metadata owner is inventory; major changes are migration-sensitive. |
| `portal/dockhand` | Single | Dockhand `v1.0.25` | Classified by the stack contract as foundational platform orchestration. |
| `portal/homepage-admin` | Single | Homepage `v1.12.3` | Shares the same repository and version with the home and media Homepage stacks. |
| `portal/homepage-home` | Single | Homepage `v1.12.3` | Shares the same repository and version with the admin and media Homepage stacks. |
| `portal/homepage-media` | Single | Homepage `v1.12.3` | Shares the same repository and version with the admin and home Homepage stacks. |
| `portal/portal-entry` | Single | Nginx `alpine` | Named moving variant rather than a versioned desired reference. |
| `portal/traefik3` | Single | Socket proxy untagged; Traefik minor track `v3.6`; Redis `latest` | Foundational domain-edge infrastructure and accepted exception; three services must preserve socket, certificate, TCP/UDP, and Redis behavior. |
| `public/audiobookshelf` | Single | Audiobookshelf `2.33.1` | Exact-version application reference. |
| `public/calibre-web-automated` | Single | Calibre-Web Automated `v4.0.6` | Exact-version application reference. |
| `public/immich` | Official vendor baseline + override | Server `release`; ML `release-cuda`; digest-pinned Valkey 9 and Postgres 14/VectorChord image | Vendor release Compose is authoritative; four coupled services, GPU variant, and stateful data images make the whole release the update unit. |
| `public/it-tools` | Single | IT-Tools untagged | Implicit `latest`; no exact desired artifact or recorded upstream authority. |
| `public/komga` | Single | Komga `1.24.3` | Exact-version application reference. |
| `public/mealie` | Single | Mealie `v3.14.0` | Exact-version application reference. |
| `public/music` | Single | Navidrome `latest`; Python minor variant `3.12-slim` for local reconciler | Accepted split-auth exception with a repo-owned reconciler and cross-stack Authentik behavior; images have different authorities and risk. |
| `public/readmeabook` | Vendor-preserving base + override | Effective ReadMeABook `1.1.8`; vendor base says `latest` | Host-bound stack; proposal must compare the vendor base while preserving the local exact image override, paths, secrets, and OIDC configuration. |
| `public/romm` | Single | RomM `4.8.1`; MariaDB minor track `11.4` | Host-bound application plus stateful database and cross-stack Authentik OIDC coupling; classified as a controlled migration surface. |
| `public/storyteller` | Single | Storyteller `web-v2.11.13` | Host-bound application with a versioned variant tag. |
| `seedbox/bittorrent` | Single | Gluetun untagged; Deunhealth untagged; qBittorrent `5.1.4`; local `ws-ephemeral` `latest` | Four coupled services share a VPN namespace; accepted exception; image authorities and risks differ, and network behavior must be preserved. |
| `seedbox/sabnzbd` | Single | SABnzbd `latest` | Host-bound download stack using a floating reference. |
| `servarr/bazarr` | Single | Bazarr `1.5.6` | Exact-version LSIO application reference. |
| `servarr/beets-flask` | Upstream-shaped base + override, source not recorded | Beets Flask `stable` | Host-bound data-processing stack; startup patches and pinned runtime plugins must be checked against image changes. |
| `servarr/jackett` | Single | Jackett `latest` | Floating LSIO application reference. |
| `servarr/kapowarr` | Single | Kapowarr `v1.3.1` | Exact-version application reference. |
| `servarr/komf` | Single | Komf `latest` | Floating application reference. |
| `servarr/lazylibrarian` | Single | LazyLibrarian `latest` | Floating LSIO application reference. |
| `servarr/lidarr` | Single | Lidarr `3.1.0` | Exact-version LSIO application reference. |
| `servarr/notifiarr` | Single + metadata | Notifiarr untagged | Portable stack metadata exists, but the image is implicit `latest` and update authority is absent. |
| `servarr/prowlarr` | Single | Prowlarr `2.3.5`; FlareSolverr `nodriver` | Application and helper have separate authorities; helper uses a named channel/variant rather than a version. |
| `servarr/radarr` | Single | Radarr `6.1.1` | Shares repository and version with `radarr-anime`, but remains a separate stack proposal. |
| `servarr/radarr-anime` | Single | Radarr `6.1.1` | Shares repository and version with `radarr`, but remains a separate stack proposal. |
| `servarr/rensaio` | Single | Rensa `latest` | Floating application reference. |
| `servarr/sonarr` | Single | Sonarr `4.0.17` | Shares repository and version with `sonarr-anime`, but remains a separate stack proposal. |
| `servarr/sonarr-anime` | Single | Sonarr `4.0.17` | Shares repository and version with `sonarr`, but remains a separate stack proposal. |

## Constraints exposed for later decisions

1. **Policy cannot be inferred safely from the current tag.** Untagged,
   `latest`, `stable`, release variables, major/minor tracks, exact tags, and
   digest pins all exist, but none state whether that shape is intentional.
2. **Authority is stack-level with per-image exceptions.** Vendor Compose
   releases govern Authentik and Immich as coordinated units, while multi-image
   repo-owned stacks such as BitTorrent and Music combine unrelated image
   authorities.
3. **The effective Compose model matters.** ReadMeABook changes the vendor
   `latest` reference to an exact version in its override; Immich changes the ML
   image to a GPU tag variant. Scanning only the base file produces false
   desired-state observations.
4. **One proposal per stack still needs shared discovery.** Homepage, Radarr,
   Sonarr, Postgres, and Authentik services reuse repositories. Candidate
   resolution can be cached, but results must be evaluated and presented in
   each stack context.
5. **Risk is not derivable from image names alone.** Foundational role,
   stateful data, host/GPU/VPN coupling, auth boundaries, local scripts and
   patches, and vendor migration instructions all change how a candidate may be
   proposed.
6. **Migration must be incremental.** Thirty-five stacks lack metadata and all
   37 lack an explicit update policy. A future validator needs a transition
   state so adding the new contract does not make unrelated stack work
   impossible.

## Repository evidence

- [`stacks/README.md`](../../stacks/README.md) defines stack ownership,
  portability tiers, vendor-preserving overrides, and foundational controlled
  migrations.
- [`docs/decisions/adr-006-stack-normalization-exceptions.md`](../decisions/adr-006-stack-normalization-exceptions.md)
  identifies behavior-sensitive exceptions.
- Stack-local READMEs document Authentik vendor preservation, Traefik
  infrastructure behavior, Music auth coupling, RomM OIDC coupling, BitTorrent
  VPN coupling, and Beets Flask runtime patches.
- Root-level `compose.yaml` and `compose.override.*` files provide the image and
  layering counts above. Files under stack `appdata/` are application
  configuration and were not counted as repo-managed stack definitions.
