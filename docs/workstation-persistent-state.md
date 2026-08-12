# Workstation Persistent State

## When You Need This

The workstation role bind-mounts selected home paths from `/ephemeral/workstation/home` so they survive an intentional LXC rebuild. Two of those paths — `~/.config/herdr` (herdr configuration, session snapshot, plugin registration, Collie's env file) and `~/.local/state/collie` (Collie runtime state) — already exist as plain directories with live data on the workstation. You need this runbook before the first `site.yml` run that enables them.

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

Collie runs as its own generated user service, so systemd stops it. herdr currently runs as a detached `herdr server` process with no user unit, so stop it through its own API:

```bash
systemctl --user stop collie
herdr server stop
```

Once the dotfiles herdr supervision slice lands, herdr gains a user service and `systemctl --user stop herdr` replaces the second command. Check with `systemctl --user list-unit-files | grep herdr` before assuming either form.

Stopping herdr ends the processes running in its panes. That is a known and accepted property of the herdr cutover, not a surprise — schedule this migration when no important pane work is in flight. Start Collie again after the deploy, and relaunch herdr the way you normally do.

`/ephemeral` is a separate filesystem, so use a copy mode that preserves ownership, modes, ACLs, and xattrs. `rsync -aHAX` does; `mv` across filesystems and plain `cp` do not.

```bash
mkdir -p /ephemeral/workstation/home/.config /ephemeral/workstation/home/.local/state

rsync -aHAX --numeric-ids ~/.config/herdr/ /ephemeral/workstation/home/.config/herdr/
rsync -aHAX --numeric-ids ~/.local/state/collie/ /ephemeral/workstation/home/.local/state/collie/
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

The `0700` mode in `workstation_persistent_home_links` applies to the mount point and the target directory only. It is never applied recursively — file modes inside, including the `0600` env file, are left exactly as they are.

Now deploy:

```bash
uv run --locked ansible-playbook site.yml --limit workstation
```

## What Is Deliberately Not Persisted

- `~/.config/systemd/user/collie.service` — Collie regenerates this unit, and it embeds checkout-specific paths. A persisted copy would pin stale paths across a rebuild.
- `~/.local/state/herdr/agent-detection` — a herdr cache, rebuilt on demand. Persisting it keeps stale detection results alive.

Note that `~/.local/state/collie` is mounted, not `~/.local/state`. Mounting the parent is what would drag the herdr cache into the contract.

## Post-Rebuild Validation

After rebuilding the LXC against the retained `/ephemeral` volume, confirm:

- herdr starts with its previous configuration, restores its session snapshot, and lists the same registered plugins.
- Collie starts with its previous configuration and the same VAPID identity (subscriptions enrolled before the rebuild remain valid).
- The enrolled Android device still receives a push notification.

Live pane processes do **not** survive an LXC rebuild — the container is replaced, so every running process is gone. That is expected and is not a validation failure. herdr restores session state reconstructively from its snapshot; it does not resume the old processes.
