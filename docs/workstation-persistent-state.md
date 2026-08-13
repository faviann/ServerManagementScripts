# Workstation Persistent State

## When You Need This

The workstation role bind-mounts selected home paths from `/ephemeral/workstation/home` so they survive an intentional LXC rebuild.

**Status: all ten declared paths are migrated and mounted as of 2026-08-13.** The migration procedure below is kept because it is the procedure for any path added to the contract later, and because the post-rebuild validation at the end is still outstanding.

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

Stopping herdr ends the processes running in its panes. That is a known and accepted property of the herdr cutover, not a surprise — schedule this migration when no important pane work is in flight. Restart both after the deploy, following [Restarting Afterwards](#restarting-afterwards) — starting `collie.service` alone is not enough.

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

Start herdr first, then Collie. Collie's bridge polls for the herdr socket and retries, so the reverse order recovers on its own — but it logs a `cannot reach Herdr socket` line that looks like a failure and is not.

```bash
# 1. herdr, however you normally launch it
# 2. Collie: the service AND its origin forwarder socket
systemctl --user start collie
systemctl --user start collie-origin-forwarder.socket
```

**Starting `collie.service` alone leaves Collie unreachable from anything but the workstation.** The bridge binds `127.0.0.1:8787` only. `collie-origin-forwarder.socket` is what binds `0.0.0.0:8788`, and 8788 is the address Traefik forwards to — see `collie-workstation` in `stacks/portal/traefik3/appdata/traefik3/config/conf.d/externalservice.yaml`. With the forwarder down, a phone gets no route and the PWA sits on "waiting for herdr", which reads like a herdr fault when herdr is fine.

The units are `enabled`, so a reboot starts them. Only a manual stop — like the migration above — leaves them down.

Verify the whole path rather than just the unit states:

```bash
systemctl --user is-active collie collie-origin-forwarder.socket   # active active
ss -tln | grep -E '8787|8788'                                      # both must be listening
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8788/    # 200
```

`8788` missing from `ss` is the signature of this failure. A `302` from `https://collie.admin.faviann.com` is success, not an error — the route carries `protected-edge-auth`, so it redirects to Authentik before reaching Collie.

## What Is Deliberately Not Persisted

- `~/.config/systemd/user/collie.service` — Collie regenerates this unit, and it embeds checkout-specific paths. A persisted copy would pin stale paths across a rebuild.
- `~/.local/state/herdr/agent-detection` — a herdr cache, rebuilt on demand. Persisting it keeps stale detection results alive.

Note that `~/.local/state/collie` is mounted, not `~/.local/state`. Mounting the parent is what would drag the herdr cache into the contract.

## Post-Rebuild Validation

After rebuilding the LXC against the retained `/ephemeral` volume, bring both back up using [Restarting Afterwards](#restarting-afterwards), then confirm:

- herdr starts with its previous configuration, restores its session snapshot, and lists the same registered plugins.
- Collie starts with its previous configuration and the same VAPID identity (subscriptions enrolled before the rebuild remain valid).
- The enrolled Android device still receives a push notification.

Do not diagnose a missing push as lost VAPID material before checking that `8788` is listening. An unreachable device and a lost identity look identical from the phone, and the forwarder being down is much the likelier cause. Collie logs `[push] enabled (N saved subscription(s))` on startup — if N is non-zero, the subscriptions survived and any failure past that point is a routing problem, not a persistence one.

Live pane processes do **not** survive an LXC rebuild — the container is replaced, so every running process is gone. That is expected and is not a validation failure. herdr restores session state reconstructively from its snapshot; it does not resume the old processes.
