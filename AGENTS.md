# Agent Operating Instructions

**Project Type**: Ansible infrastructure-as-code (IaC)  
**Purpose**: Automate Proxmox LXC provisioning, configuration, and service deployments  
**Architecture**: Portable workstation-based (runs from any Linux workstation with network access to Proxmox)

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (faviann/homelab-iac) via the `gh` CLI; external PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles use their default label strings (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root (created lazily by `/domain-modeling`). See `docs/agents/domain.md`.

## Project Philosophy

**Code is a liability, not an asset.** When two approaches exist, recommend the one with less code and fewer objects.

## Non-negotiables
- Never request, paste, or print secrets (API token secret, vault passphrase, private keys). Use placeholders like `<REPLACE_ME>` in docs or examples.
- Run Python and Ansible tools through `uv run --locked <tool>`. If `.venv/` does not exist, run `uv sync --locked`.
- `ansible.cfg` expects the vault passphrase at `~/.ansible/vault-pass`.
- Lifecycle playbooks skip any host whose `inventory_hostname` matches the controller's hostname (`proxmox_skip_self: true` by default). To manage the control node intentionally: `./run.sh -e proxmox_skip_self=false --limit workstation` (`--limit` targets the host, `-e` disables the guard).

## Standard Paths

| Item | Location |
|------|----------|
| SSH key (private) | `~/.ansible/ssh/proxmox_lxc` (home-dir, machine-local, shared across worktrees) |
| SSH key (public) | `~/.ansible/ssh/proxmox_lxc.pub` (home-dir, machine-local, shared across worktrees) |
| Vault password | `~/.ansible/vault-pass` (home-dir, written by chezmoi from Bitwarden) |
| Vaulted secrets | `inventory/group_vars/all/vault.yml` (encrypted) |
| Fact cache | `.ansible/cache/` (project-relative, gitignored, 1h TTL) |
| Venv | `.venv/` (project-relative, gitignored) |
| External roles | `.ansible/roles/` (project-relative, gitignored, auto-installed) |

Secrets are only in encrypted `inventory/group_vars/all/vault.yml` — never commit plaintext credentials. The vault password file is machine-local and should be provisioned outside this repo.

## Inventory Structure

| Type | Groups | Purpose |
|------|--------|---------|
| **Tiers** | `tier_tiny`, `tier_small`, `tier_medium`, `tier_large` | Resource allocation (mutually exclusive) |
| **Capabilities** | `cap_docker`, `cap_gpu`, `cap_wireguard` | Sets feature flags: `docker_enabled`, `gpu_enabled`, etc. |
| **Special** | `proxmox_api`, `lxcs` | API controller + all LXC targets |

**Naming**: LXCs resolve as `{{ inventory_hostname }}.faviann.vms`

**Feature Flags**: Capability groups set boolean flags (`docker_enabled: true`) instead of checking group membership. Roles use `when: docker_enabled | default(false)`, never `'cap_docker' in group_names`.

→ [docs/inventory-structure-guide.md](docs/inventory-structure-guide.md) — read for variable precedence order, worked examples, and adding new hosts.

## Deployment Lifecycle

`site.yml` runs three phases in sequence: **validate** → **provision** (LXC create/update via Proxmox API) → **configure** (in-container: packages, Docker, stacks). Two-tier host config: `proxmox_lxc_provision` handles API-allowed settings; `proxmox_lxc_host_config` handles restricted features (`keyctl=1`, `nesting=1`) via `pct` on the Proxmox host.

Run lifecycle operations through `./run.sh`. It serializes lifecycle mutation with one machine-local lock shared by every worktree on the workstation; it does not coordinate runs from different control nodes.

Roles live in `playbooks/roles/{base,infrastructure,provisioning,config}/`.

## Docker Stacks

Stacks live in `stacks/<hostname>/<stack-name>/compose.yaml`. Auto-discovered and started with `docker compose up -d` — no registration needed.

→ [stacks/README.md](stacks/README.md) — read for stack contract, Traefik routing, secrets, and full conventions.

## Command Reference

| Command | Purpose |
|---------|---------|
| `./validate.sh` | Complete non-live verification — lint, full lifecycle regressions, and full pytest suite |
| `./run.sh` | Full lifecycle — deploy/update all LXCs |
| `./run.sh -e proxmox_skip_self=false --limit workstation` | Intentionally include the control node when running from `workstation` |
| `./run.sh --limit <host>` | Target one host |
| `./run.sh --limit <host> -e stack_filter=<stack>` | Deploy one stack on a host (skips all others) |
| `./run.sh --check` | Dry run |
| `uv run --locked ansible-playbook bootstrap.yml` | Recreate bootstrap artifacts after clean install |
| `./validate.sh lifecycle` | Fast lifecycle feedback (~1.5 min) — semantic lifecycle facade matrix + targeted planning barrier, controlled observations only. Run while iterating on LXC lifecycle changes |
| `./validate.sh lifecycle --only <launcher.py>` | Target one registered lifecycle launcher in the same credential-free fixture environment. Repeat `--only` to run several launchers in the supplied order |
| `./validate.sh lifecycle --full --fail-fast` | Remediation pass — finish the concurrent fast launchers, then stop scheduling after the first observed failure |
| `./validate.sh lifecycle --full` | Full lifecycle regression set (~6 min) — fast path plus host-config idempotence, real role-composition wiring, fleet preflight, and contract seams. Prefer `./validate.sh` for handoff verification |
| `./validate.sh lint` | Targeted repo-wide lint feedback (production profile). Prefer `./validate.sh` for handoff verification |
| `./validate.sh tests [<target>...]` | Run the test suite, optionally restricted to targets inside `tests/` (a path, optionally with a `::` node-id suffix). Targets outside the test tree are invalid usage |
| `./validate.sh stack <path>` | Validate one repo-managed stack's update policy — schema-versioned JSON on stdout, diagnostics on stderr. Not part of the no-argument handoff run |
| `./setup.sh` | Fresh workstation setup — extend here for new workstation config (editor, tooling, env) |
| `ssh -l root -i ~/.ansible/ssh/proxmox_lxc <host>` | Direct SSH into an LXC |

**Timing**: `uv run --locked ansible-playbook` runs against live hosts typically take 5–10 minutes. Do not assume a hang — wait for completion before acting on the result.

For lifecycle-regression remediation, use repeatable `./validate.sh lifecycle --only <launcher.py>` for the shortest targeted loop and add `--fail-fast` when later selected launchers cannot provide useful evidence after a failure. `--only` accepts the registered filenames reported by the runner's actionable error, and cannot be combined with `--full`. A targeted operation never substitutes for the full handoff run: before handoff, always run `./validate.sh` with no arguments so every gate reports a result.

Run `./validate.sh` for complete deterministic handoff verification. It does not load live inventory or acquire the lifecycle lock. Route every operation that contacts managed hosts, including `--check`, through `./run.sh`.

**Long-running output discipline**: For live deploys or other noisy commands, avoid streaming full output into chat context. Prefer redirecting to a temp log and polling only high-signal excerpts:
```bash
./run.sh --limit <host> > /tmp/<task>.log 2>&1
tail -40 /tmp/<task>.log
rg "failed=|unreachable=|FAILED|changed=|<relevant-resource>" /tmp/<task>.log
```
Only read the full log when the summarized output is insufficient to diagnose a failure. Never print secrets from logs or vault output.

Debug: `./run.sh -vvv` for verbose output, `./inspect.sh vars <name>` for merged vars, `uv run --locked ansible -i inventory/hosts.yml lxcs -m ping` for connectivity, delete `.ansible/cache/` for stale facts.

## Role Design Principles

- One role = one concern; use `meta/main.yml` for dependencies
- Use feature flags (`docker_enabled`) not group checks (`'cap_docker' in group_names`)
- Avoid hardcoded values; inject via vars. Ensure idempotency; use `assert` to fail fast

## Related Documentation

→ [docs/inventory-structure-guide.md](docs/inventory-structure-guide.md) — read when adding hosts or debugging variable precedence.
→ [stacks/README.md](stacks/README.md) — read when creating or modifying Docker stacks.
→ [setup.sh](setup.sh) — read when addressing workstation tooling, editor config, or environment setup for contributors.
→ [docs/workstation-persistent-state.md](docs/workstation-persistent-state.md) — read before any workstation deploy that enables persistent home mounts.
