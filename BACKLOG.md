# Backlog

## [DES-006] Configure OIDC for Storyteller
- **Category**: design
- **Location**: `stacks/public/storyteller/`
- **Context**: Storyteller stack was deployed without OIDC; needs Authentik provider and application wired up like other SSO-enabled stacks.
- **Added**: 2026-04-19

## [DES-007] Secret plumbing: each secret named 4x from vault to container
- **Category**: design
- **Location**: `inventory/group_vars/all/vault.yml`, `inventory/host_vars/*.yml`, `playbooks/roles/config/lxc_stack_sync/tasks/materialize.yml`, `stacks/**/.env.j2`
- **Context**: 2026-07-05 architecture review — adding one secret means editing three files in lockstep (`vault_*` key, hand-written `lxc_docker_env_stack_vars` binding, `stack_vars.*` reference); `host_vars/auth.yml` alone hand-maps ~25 vault vars. A naming-convention resolution inside `lxc_stack_sync` would delete the mapping blocks but trades explicitness for magic and is in tension with the stack_sync README's ban on injecting stack metadata into host var scope. Needs a design discussion before any code; see `docs/plans/2026-07-05-architecture-cleanup.md` (Not in scope).
- **Added**: 2026-07-05

## [DES-008] Lifecycle fail-fast latch is read twice across a concurrency window
- **Category**: design
- **Location**: `playbooks/roles/provisioning/proxmox_lxc_lifecycle/tasks/execute.yml`
- **Context**: Found 2026-08-16 while removing sudo escalation from the lifecycle fixtures (commit `c0fd59f`). `hostvars.localhost.proxmox_lifecycle_execution_failure` gates two sibling tasks — the `not_executed` suppression publisher (`length > 0`) and the execution block (`length == 0`). A host that evaluates the first before another host sets the latch and the second after it publishes *neither*, silently keeping its planning outcome (`status: planned`) instead of an execution outcome. The role's own comment assumes `serial: 1`, which makes the reads deterministic, but the facade fixtures run 24 scenarios with `strategy: free` and `ANSIBLE_FORKS=25`; one sudo failure there produced ten hosts stuck at `planned`. Currently latent — nothing fails while no host errors inside the window — so the fixtures pass without exercising it. A single read latched into a fact before the branch, or an explicit else-publisher, would close the gap.
- **Added**: 2026-08-16

## [TEST-001] Vault and setup shell scripts lack focused tests
- **Category**: missing-test
- **Location**: `rotate-vault-passphrase.sh`, `configure-vault.sh`, `setup.sh`
- **Context**: 2026-07-05 architecture review — the vault/setup shell scripts (~800+ lines combined) have no focused tests and remain a high-risk untested surface.
- **Added**: 2026-07-05

