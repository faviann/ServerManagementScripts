---
name: rename-stack
description: Use when renaming a repo-managed Docker Compose stack under stacks/ (folder, FQDN, and its running deployment), including explicit /rename-stack requests.
---

# Rename Stack

Rename `stacks/<host>/<old>` → `stacks/<host>/<new>` so the repo, the FQDN, and the running deployment
all move together **without losing persistent state**.

## Why this needs a skill

The mechanics are non-obvious and partly automated:

- **Remote stop is already automatic.** `lxc_stack_sync` computes `stale = deployed − desired` and, for
  each stale folder, runs `docker compose down --remove-orphans` then moves it to
  `/shared/<host>/stale-stacks/<name>-<timestamp>`. A naive rename + full deploy stops the old stack —
  but starts the new one with **empty appdata**.
- **The FQDN changes for free.** Default routing is `Host(` `<compose-project>.<default_domain>` `)` and
  the role injects `stack_name` = folder name. For ordinary stacks the folder rename *is* the FQDN
  change. Only a hardcoded `Host(...)` rule or a literal FQDN in `.env` needs editing.
- **State is the hazard.** `./appdata/...` binds live inside the stack folder; named volumes are
  project-name-prefixed. The safe primitive is to **`mv old new` in place on the remote** (bind appdata
  rides along) **then full-host deploy**.

## First reads

- `stacks/README.md` — stack contract, portability tiers, Traefik defaults.
- `.agents/skills/create-stack/SKILL.md` — reuse its Review Checklist on the moved stack.

## Refuse these (foundational / OIDC-coupled)

Stop and tell the user to run a **dedicated migration** (`stacks/README.md` → "Foundational controlled
migration"), do **not** auto-rename, when any is true:

- host is `auth` or `portal`.
- folder is `auth`, `romm`, or `immich`.
- the stack has a top-level named `volumes:` block (project-prefixed volumes do not follow a rename).
- the stack is listed in `docs/decisions/adr-006-stack-normalization-exceptions.md`.
- the stack is referenced by Authentik blueprints (`rg -n '<old>' stacks/auth/auth/appdata/authentik/`).

These stacks have cross-host coupling (OIDC redirect URIs, hardcoded paths, portal discovery) that a
generic rename cannot safely reconcile.

## Workflow

### 1. Parse & guard

- Resolve `stacks/<host>/<old>`. Assert it exists and `stacks/<host>/<new>` does not.
- Run the refusal checks above.
- Detect storage shape to pick the preservation path:
  | Shape | Path |
  | --- | --- |
  | bind `./appdata/...` | preserved by in-place `mv` — normal path |
  | named `volumes:` | refuse (see above); volumes won't follow the rename |
  | only `/data` or `/ephemeral` | nothing to migrate; in-place `mv` is still correct |

### 2. Repo edits (do automatically, then preview — **gate 1**)

- `git mv stacks/<host>/<old> stacks/<host>/<new>`.
- **Reference hunt.** `rg -n '<old>'` across the repo; fix every hit that means *this* stack:
  - `stack.yaml` `name:` field; stack-local `README.md` / `docs/**`.
  - explicit `traefik.http.routers.<name>...Host(...)` rules and `traefik.domain` in the stack's compose.
  - Traefik router/service names embedded in label keys, e.g. `traefik.http.services.<old>.loadbalancer...`
    (cosmetic — the router/service name is stack-local — but normalize for consistency).
  - `container_name:` if it embeds the old name (cosmetic; not required for correctness).
  - literal FQDNs hardcoding the old name in `.env` / `.env.j2`.
  - `inventory/host_vars/<host>.yml` — secret-binding keys / `lxc_docker_env_stack_vars` entries keyed by
    the stack name.
  - homepage labels referencing the old name.
  - cross-stack references in other stacks on any host.
- Apply the `create-stack` Review Checklist to the moved stack (Traefik labels on the user-facing service
  only, etc.).
- Show the full `git diff` for approval.

### 3. Remote migration + deploy (present, run on approval — **gate 2**)

Look up `docker_uid`/`docker_gid` for the host if a `chown` is needed
(`uv run --locked ansible-inventory -i inventory/hosts.yml --host <host> --yaml`).

```bash
# Stop old stack and rename folder in place (bind appdata rides along).
ssh -l root -i ~/.ansible/ssh/proxmox_lxc <host> \
  'cd /shared/<host>/stacks/<old> && docker compose down; \
   mv /shared/<host>/stacks/<old> /shared/<host>/stacks/<new> && \
   chown -R <docker_uid>:<docker_gid> /shared/<host>/stacks/<new>'

# Full-host deploy — NOT stack_filter (it suppresses stale detection and skips reconciliation).
./run.sh --limit <host> > /tmp/rename-<new>.log 2>&1
tail -40 /tmp/rename-<new>.log
rg "failed=|unreachable=|quarantin|<new>" /tmp/rename-<new>.log
```

- **Use a full-host deploy.** `-e stack_filter=` short-circuits `stale` handling (`discover.yml` →
  "Suppress stale stack list when stack_filter is active") and would leave orphans if the in-place `mv`
  were ever skipped.
- After the in-place `mv`, `<old>` no longer exists on the remote → not stale → nothing quarantined;
  `<new>` is desired + deployed → re-materialized and started with its existing appdata.

### 4. External follow-ups (print only when the FQDN changed and the stack was user-facing)

- Bookmarks / external links to the old FQDN. `traefik-kop` Redis may hold the stale route until the old
  container is gone (it is, after `down`).
- **Traefik SANs are not affected by a same-tier rename** — only a new subdomain *tier* needs a SAN in
  `stacks/portal/traefik3/appdata/traefik3/config/traefik.yaml`. Call this out so the user does not
  over-edit.
- Authentik OIDC redirect URIs would matter for auth-coupled apps — but those are refused in step 1, so
  this is a reminder of *why* the refusal fired, not a step to perform here.

## Verify

1. Repo: folder moved, no stray hits — `rg -n '<old>' stacks/ inventory/ docs/`.
2. Routing followed the rename — `curl -sSI https://<new>.<default_domain>` returns the app.
3. On the LXC: `ls /shared/<host>/stacks` shows `<new>` not `<old>`; `ls /shared/<host>/stale-stacks`
   shows **nothing new** (proves the in-place `mv` path, not a quarantine/fresh-start).
4. State preserved: the app keeps its prior config/state (login, library, settings intact).
