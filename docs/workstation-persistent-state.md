# Workstation Persistent State

## When You Need This

The workstation role bind-mounts selected home paths from `/ephemeral/workstation/home` so they survive an intentional LXC rebuild.

**Status: all ten declared paths are migrated and mounted as of 2026-08-13, and rebuild persistence was validated 2026-08-15/16.** The migration procedure below is kept because it is the procedure for any path added to the contract later.

Note that `~/.claude.json` is a sibling of the mounted `~/.claude` and is therefore *not* covered — see #157.

The two paths migrated last were `~/.config/herdr` (herdr configuration, session snapshot, plugin registration, Collie's env file) and `~/.local/state/collie` (Collie runtime state). Read this runbook before the first `site.yml` run that enables any newly declared path.

## The Fail-Closed Assert

`playbooks/roles/config/lxc_workstation_baseline/tasks/persistent_home.yml` refuses to mount over a home path that already exists as a plain directory and is neither a symlink to the target nor already a bind mount. The run fails with:

```
/home/faviann/.config/herdr exists and is not the managed bind mount path from
/ephemeral/workstation/home/.config/herdr. Move or migrate it manually before enabling
workstation persistent home mounts.
```

This is deliberate. A bind mount hides whatever is underneath it, so mounting over populated directories would silently orphan the existing data instead of persisting it. The role stops and makes a human decide.

## Pre-Deploy Migration

Run these on the workstation, as the workstation user, before `site.yml --limit workstation`.

Stop both first. herdr keeps live Unix sockets in `~/.config/herdr` and rewrites `session.json` as panes change; Collie writes into `~/.local/state/collie`. Copying either directory while it is running captures a torn session snapshot, and a graceful stop is what makes herdr write its final one.

Collie runs as its own generated user service, plus an origin-forwarder service and its activating socket unit. Stop the socket first — leaving it active lets the next connection reactivate the service underneath the copy. herdr currently runs as a detached `herdr server` process with no user unit, so stop it through its own API:

```bash
systemctl --user stop collie-origin-forwarder.socket collie-origin-forwarder.service
systemctl --user stop collie
herdr server stop
```

Once the dotfiles herdr supervision slice lands, herdr gains a user service and `systemctl --user stop herdr` replaces the last command. Check with `systemctl --user list-unit-files | grep herdr` before assuming either form.

Confirm nothing came back before copying:

```bash
systemctl --user list-units --state=active | grep -E 'collie|herdr'   # expect no output
pgrep -af 'herdr|collie'                                              # expect no output
```

Stopping herdr ends the processes running in its panes. That is a known and accepted property of the herdr cutover, not a surprise — schedule this migration when no important pane work is in flight. Restart both after the deploy, following [Restarting Afterwards](#restarting-afterwards).

**herdr does not use tmux.** It runs its own panes as direct children of the `herdr server` process, so `tmux list-sessions` tells you nothing about what you are about to kill. List the real panes and what is running in them:

```bash
for p in $(pgrep -P "$(pgrep -f 'herdr server')"); do
  printf '%-8s ' "$(ps -o tty= -p "$p")"
  ps -o args= -p $(pgrep -P "$p") 2>/dev/null || echo '<idle>'
done
```

`/ephemeral` is a separate filesystem, so use a copy mode that preserves ownership, modes, ACLs, and xattrs. `cp -a` implies `--preserve=all` and does; a bare `cp` drops them. Use coreutils rather than rsync — rsync is not a workstation package, so it is not there on a freshly rebuilt LXC.

```bash
mkdir -p /ephemeral/workstation/home/.config /ephemeral/workstation/home/.local/state

cp -a ~/.config/herdr /ephemeral/workstation/home/.config/herdr
cp -a ~/.local/state/collie /ephemeral/workstation/home/.local/state/collie
```

Collie's env file holds the VAPID private key at mode `0600`. Verify it survived before moving anything aside:

```bash
stat -c '%a %U:%G %n' \
  /ephemeral/workstation/home/.config/herdr/plugins/config/herdr.collie/.env
# expect: 600 faviann:faviann ...
```

Then move the originals aside so the assert sees empty (or absent) mount points. Rename rather than delete — a failed migration is then recoverable:

```bash
mv ~/.config/herdr ~/.config/herdr.pre-persist
mv ~/.local/state/collie ~/.local/state/collie.pre-persist
```

Once the post-rebuild validation below passes, delete the `.pre-persist` copies.

The `0700` mode in `workstation_persistent_home_links` applies to the mount point and the target directory only. It is never applied recursively — file modes inside, including the `0600` env file, are left exactly as they are. Note this can *tighten* an existing directory: `~/.config/herdr` was `0775` before the mount and is `0700` after.

## Deploying

The workstation is the Ansible control node, and `proxmox_skip_self` defaults to true — so a run launched here skips the workstation and reports success having changed nothing. Pass `-e proxmox_skip_self=false` to target it deliberately. Drive it from a plain SSH shell, not from inside a herdr pane.

**Check before applying.** This is the load-bearing step, not an optional dry run:

```bash
uv run --locked ansible-playbook site.yml --check \
  -e proxmox_skip_self=false --limit workstation > /tmp/ws-check.log 2>&1
rg "restart_required|failed=|unreachable=" /tmp/ws-check.log
```

Require zero failed and zero unreachable, and no host-config restart. Before a migration this check *fails* at the persistent-home guard — that is expected, and is the "before" evidence.

Why the restart matters more than anything else: `proxmox_lxc_host_config/tasks/main.yml` issues `pct reboot` from the Proxmox host when any host-config component reports `restart_required`, and it fires early, before guest configuration. A container restart kills any Ansible process running inside the container, so the run dies before reaching the workstation role. `restart_required` is computed in check mode, so the check reports it truthfully in advance.

**Detach the apply**, so an SSH disconnect cannot kill it. Lingering is enabled and `KillUserProcesses` is unset, so a transient user unit outlives the session:

```bash
systemd-run --user --unit=ws-deploy --collect \
  bash -lc 'cd ~/repos/homelab-iac/<worktree> && \
    uv run --locked ansible-playbook site.yml \
    -e proxmox_skip_self=false \
    -e lxc_base_system_reboot_enabled=false \
    --limit workstation > /tmp/ws-deploy.log 2>&1'

journalctl --user -u ws-deploy -f
systemctl --user show ws-deploy -p ExecMainStatus   # 0 when it finished cleanly
```

`-e lxc_base_system_reboot_enabled=false` suppresses the end-of-run guest reboot, which fires when `/var/run/reboot-required` exists — an `apt` upgrade during the run can create it. Reboot deliberately afterwards once the recap is clean.

Detaching survives an SSH drop. It does not survive a container restart — nothing running inside the container does. That is why the check comes first.

**The LXC will not be destroyed by this run.** `proxmox_lxc_rebuild_on_release_mismatch` and `proxmox_lifecycle_allow_destructive_transitions` are both `false` in `inventory/group_vars/all/proxmox.yml` and unset in `host_vars/workstation.yml`, so the planner cannot emit `rebuild` or `remove`, and an unauthorized destructive plan fails the run rather than executing it. The expected transition for a running workstation is `provision`, non-destructive.

Afterwards, confirm the mounts are actually live rather than trusting the play recap — an unmounted bind mount is an empty directory, not an error:

```bash
findmnt ~/.claude ~/.codex ~/.agents ~/.pi ~/.config/agent-of-empires \
        ~/.hermes ~/.openclaw ~/.config/herdr ~/.local/state/collie ~/repos
```

Every declared path must appear. A missing row is an unmounted bind mount, and the play recap will not have flagged it.

## Restarting Afterwards

Start herdr, then `systemctl --user start collie`. Collie pulls up its origin socket through `Wants=collie-origin-forwarder.socket`, so that is the whole sequence. Starting Collie first also works — its bridge polls for herdr and retries — but it logs a `cannot reach Herdr socket` line that looks like a failure and is not.

`Wants` is soft, so a socket that fails to bind leaves Collie started and reporting healthy while nothing outside can reach it. Confirm the listener rather than the unit state:

```bash
ss -tln | grep 8788
```

Nothing there is the signature of an unreachable Collie: the phone gets no route and the PWA sits on "waiting for herdr", which reads like a herdr fault when herdr is fine.

## What Is Deliberately Not Persisted

- `~/.config/systemd/user/collie.service` — Collie regenerates this unit, and it embeds checkout-specific paths. A persisted copy would pin stale paths across a rebuild.
- `~/.local/state/herdr/agent-detection` — a herdr cache, rebuilt on demand. Persisting it keeps stale detection results alive.

Note that `~/.local/state/collie` is mounted, not `~/.local/state`. Mounting the parent is what would drag the herdr cache into the contract.

## Rebuilding the LXC

**Status: validated 2026-08-15/16.** The procedure below is what was actually run; see #95 for the recorded observations.

### The planner cannot be asked for a rebuild

`proxmox_lxc_lifecycle/tasks/decide.yml` computes `rebuild_required` as a release mismatch **and** `proxmox_lxc_rebuild_on_release_mismatch`. There is no manual override. When the guest release already matches the ostemplate — the normal case — setting both destructive policy flags to `true` still yields a `provision` transition and destroys nothing.

So destroy the container out-of-band, in the Proxmox web UI. With it absent, the planner emits the ordinary create path:

```
container_transition: provision
destructive: false
reason: "Container is absent from Proxmox, so it will be provisioned."
```

Both policy flags stay `false` and nothing needs authorizing or restoring afterwards.

**`/ephemeral` is not at risk.** `mp2: "/ephemeral,mp=/ephemeral"` is a Proxmox host path, not a container-owned volume, so destroy cannot reclaim it. `mp0` belongs to container 200, so "Destroy unreferenced disks owned by guest" leaves it alone too. The safeguard that matters is confirming you are destroying **vmid 306 / `workstation`**.

### Before destroying

Record a "before" manifest. Once the container is gone, the only evidence of the prior state is what you wrote down, and the acceptance criteria ask that herdr restores the *same* plugins and Collie keeps the *same* identity:

```bash
sha256sum ~/.config/herdr/session.json ~/.config/herdr/plugins.json \
          ~/.config/herdr/plugins/config/herdr.collie/.env \
          ~/.local/state/collie/push-subscriptions.json
```

Hash the VAPID `.env`; never copy or print it. It is the one genuinely irreplaceable artifact — deliberately absent from Bitwarden, and existing only on `/ephemeral`.

A graceful herdr/Collie shutdown is **not** required here. The migration procedure above needs one because `cp -a` reads the directory for seconds while herdr writes into it; a destroy interrupts at most one in-flight write. Testing the crash path is also the more honest test, since a real rebuild will not be preceded by a polite shutdown.

Also bank `~/.ansible/ssh/proxmox_lxc` somewhere off the container — see the recovery table below for why.

### Drive it from another machine

Not from the workstation. The container is replaced, so anything running inside it dies with it — including the Ansible run.

The manual destroy skips the cleanup that `provision.yml` performs on the planner's rebuild path, so do it yourself on the driving machine:

```bash
rm -f .ansible/cache/*_workstation
ssh-keygen -R workstation && ssh-keygen -R workstation.faviann.vms
```

Then deploy. Skip `--check`: it is load-bearing when an existing container might report `restart_required`, but with no container to observe it cannot tell you anything.

```bash
uv run --locked ansible-playbook site.yml --limit workstation \
  -e proxmox_skip_self=false -e lxc_base_system_reboot_enabled=false
```

`lxc_hwaddr` is pinned in `host_vars/workstation.yml`, so the container returns on the same MAC and address.

### What does not come back on its own

| Path | Recovery |
|---|---|
| `~/.local/share/chezmoi`, `~/.ssh/id_ed25519`, `~/.ssh/config`, `~/.ssh/allowed_signers`, `~/.ansible/vault-pass` | `workstation-setup`, one Bitwarden unlock |
| `~/.config/gh` | Same unlock — the token is read from Bitwarden and piped to `gh auth login --with-token`. There is no interactive `gh` login. |
| `~/.local/state/workstation-setup/complete` | Rewritten by `workstation-setup`, which is offered automatically on your first interactive SSH login |
| `~/.ansible/ssh/proxmox_lxc` | **Manual copy.** Neither persisted nor chezmoi-managed, and `validate_environment` does not check it, so setup reports ready without it. |
| `collie.service` | Regenerated by the herdr plugin's `start` action — see below |

Do **not** recover `proxmox_lxc` by running `bootstrap.yml` on the rebuilt workstation. `control_node_bootstrap/tasks/ssh_key.yml` uses `openssh_keypair`, which generates when the file is absent, producing a new key that no LXC in the fleet trusts.

`chezmoi apply` will not clobber the copied key: the source directories are `private_dot_ansible` and `private_dot_ssh`. `private_` only forces `0700` — it is not `exact_`, so unmanaged files inside are left alone.

### Starting Collie

Collie's bridge does not auto-start with the herdr server, and `collie.service` is deliberately not persisted (it embeds the plugin install hash and a nix profile path). After a rebuild the forwarder listens on 8788 with no backend, and connections fail with `Connection refused`. Start it through the plugin:

```bash
herdr plugin action invoke start --plugin herdr.collie
```

Note the argument order — the `--plugin` option must follow the action id. This regenerates and enables `collie.service` and launches the bridge on 8787.

Then verify the origin forwarder the same way as after an ordinary deploy — `ss -tln | grep 8788`. See [Restarting Afterwards](#restarting-afterwards) for why the unit states alone do not tell you the bridge is reachable.

Tracked as faviann/dotfiles#84.

### Post-Rebuild Validation

Confirm:

- Every declared path appears in `findmnt` (check one target per invocation; an unmounted bind mount is an empty directory, not an error, and the play recap will not flag it).
- The four hashes from the before-manifest are unchanged.
- herdr restores its session — `herdr-server.log` reports `session restore evaluated … workspaces=N`.
- herdr lists the same registered plugins.
- Collie starts with the same VAPID identity. An unchanged `.env` hash is the proof: had the identity been lost, Collie would have minted a new keypair and rewritten the file.
- The enrolled Android device receives a push without re-enrollment.

Do not diagnose a missing push as lost VAPID material before checking that `8788` is listening. An unreachable device and a lost identity look identical from the phone, and the forwarder being down is much the likelier cause. Without a before-manifest to compare the `.env` hash against, Collie's startup line `[push] enabled (N saved subscription(s))` is the fallback proof: a non-zero N means the subscriptions survived.

Live pane processes do **not** survive an LXC rebuild — the container is replaced, so every running process is gone. That is expected and is not a validation failure. herdr restores session state reconstructively from its snapshot; it does not resume the old processes.

Stale unix sockets (`herdr.sock`, `herdr-client.sock`) persist on `/ephemeral` pointing at the destroyed server. herdr rebinds them cleanly on startup; they need no cleanup.
