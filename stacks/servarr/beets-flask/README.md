# beets-flask

beets-flask runs on `servarr` as the UI for tagging and importing soundtrack albums with Beets.

## Ownership

Lidarr owns the regular `Music/` library. It downloads, organizes, and tracks normal music albums there.

Beets owns `Soundtracks/`. Imports through beets-flask may tag, write metadata, and move files into the soundtrack paths below.

Any future Lidarr enrichment hook is metadata-only. It must not ask Beets to move or reorganize files that Lidarr manages under `Music/`.

## Path Contract

The stack mounts these paths with identical host and container paths:

```text
/data/media/_ingest/music:/data/media/_ingest/music
/data/media/music:/data/media/music
```

Do not remap either path inside the container. Beets stores absolute paths in `/config/beets/library.db`, so host/container path skew will create bad library entries.

The GUI inbox starts at `/data/media/_ingest/music`. The Beets library root is `/data/media/music`.

## Routing Contract

Beets path rules are order-sensitive:

```yaml
paths:
  "genre:=Game": "Soundtracks/Game/$album ($year)/$track - $title"
  "style:Video": "Soundtracks/Game/$album ($year)/$track - $title"
  "albumtype:soundtrack": "Soundtracks/Screen/$album ($year)/$track - $title"
  default: "Music/$albumartist/$album ($year)/$track - $title"
```

VGMplug maps the VGMdb `category` field to Beets `genre`, so game albums route with exact `genre:=Game`.

Discogs can mark game releases with `style` values that include `Video Game Music`; those route to the same game soundtrack path as VGMdb game releases. Use the shorter `style:Video` path query because Beets path matching does not apply the full multi-word `style:Video Game Music` phrase reliably during move/import destination evaluation.

Do not use `albumtype2`; VGMplug does not emit it in this runtime. Avoid substring queries like `genre:Game`; use exact `genre:=Game` so non-game genres containing that word do not match.

## Runtime Notes

The installed plugin module is `VGMplug`, not `vgmdb`.

Runtime dependencies are installed from `/config/requirements.txt`:

```text
beets-vgmdb==1.3.2
pyacoustid==1.3.1
python3-discogs-client==2.8
```

`appdata/startup.sh` installs Alpine `chromaprint`, installs the Python requirements, applies the compatibility patch needed by this Beets image, and verifies:

```sh
python -c "import beetsplug.VGMplug"
```

`startup.sh` also patches `VGMplug.py` at container start to add `timeout=5` to both `requests.get` calls. Without this, vgmdb.info hangs silently when down, blocking each search query for ~25–30s and pushing previews past the 30s frontend timeout.

There is intentionally no local `Dockerfile` or `stack.yaml`.

### MusicBrainz is a plugin, not built-in

In beets 2.x, MusicBrainz must be explicitly listed in `plugins:`. It is not auto-loaded. Without it, previews only query VGMdb, AcoustID, and Discogs — MB is silently skipped and most albums find no candidates.

### VGMplug autosearch and vgmdb.info

`VGMplug` with `autosearch: true` queries `https://vgmdb.info` (an unofficial JSON API mirror, not vgmdb.net). Check availability:

```bash
curl -si "https://vgmdb.info/album/12921?format=json" | head -1
```

When down, VGMplug returns no candidates but does not block imports thanks to the 5s timeout patch.

### Incremental import log

Beets tracks imported paths in `/config/beets/state.pickle` (`taghistory` key). After import+undo cycles a path stays recorded and beets skips it on re-preview ("No files imported"). Clear a specific entry:

```bash
docker exec beets-flask-beets-flask-1 python3 - <<'EOF'
import pickle
with open("/config/beets/state.pickle", "rb") as f:
    state = pickle.load(f)
target = (b"/data/media/_ingest/music/<FOLDER_NAME>",)
state["taghistory"] = {e for e in state["taghistory"] if e != target}
with open("/config/beets/state.pickle", "wb") as f:
    pickle.dump(state, f)
print("done, entries remaining:", len(state["taghistory"]))
EOF
```

### Frontend timeout is cosmetic

"Timeout: Waiting for a job update took longer than 30 seconds" is a frontend Socket.IO listener timeout hardcoded in the compiled JS. The Redis job continues and completes in the background — the import is not lost.

### move: yes is not supported

beets-flask logs a warning at import time: "does not yet support other import modes than copy". The beets config uses `move: yes`. This may cause unexpected behaviour and should be resolved.

## Safe Remediation

### Fix a misrouted game soundtrack

MusicBrainz tags game soundtracks with `genre: Soundtrack`, which routes to `Soundtracks/Screen/`. Since vgmdb.info has been down long-term, manually correct the genre and move:

```bash
docker exec beets-flask-beets-flask-1 beet modify -y -a album:"<ALBUM_NAME>" genre="Game"
docker exec beets-flask-beets-flask-1 beet move -y album:"<ALBUM_NAME>"
```

For a misrouted album, inspect metadata before moving anything:

```bash
ssh -o BatchMode=yes -l root -i ~/.ansible/ssh/proxmox_lxc servarr.faviann.vms \
  'cd /conf/docker/stacks/beets-flask && docker compose exec -T beets-flask beet -c /config/beets/config.yaml ls -a -f "$album | $genre | $path" "album:<ALBUM_NAME>"'
```

Run a pretend move and confirm the destination:

```bash
ssh -o BatchMode=yes -l root -i ~/.ansible/ssh/proxmox_lxc servarr.faviann.vms \
  'cd /conf/docker/stacks/beets-flask && docker compose exec -T beets-flask beet -c /config/beets/config.yaml move -p "album:<ALBUM_NAME>"'
```

Only after the pretend output is correct, run the real move:

```bash
ssh -o BatchMode=yes -l root -i ~/.ansible/ssh/proxmox_lxc servarr.faviann.vms \
  'cd /conf/docker/stacks/beets-flask && docker compose exec -T beets-flask beet -c /config/beets/config.yaml move "album:<ALBUM_NAME>"'
```

## Deploy

Local contract checks:

```bash
./validate.sh tests tests/unit/test_servarr_beets_flask_contract.py
./validate.sh tests tests/regression/test_beets_flask_stack_contract.py
```

Dry run one stack:

```bash
./run.sh --limit servarr --check -e stack_filter=beets-flask
```

Deploy one stack:

```bash
./run.sh --limit servarr -e stack_filter=beets-flask
```

For noisy live deploys, redirect to a temp log and inspect high-signal output:

```bash
./run.sh --limit servarr -e stack_filter=beets-flask >/tmp/beets-flask-deploy.log 2>&1
tail -40 /tmp/beets-flask-deploy.log
rg "failed=|unreachable=|FAILED|beets-flask|config.yaml|changed=" /tmp/beets-flask-deploy.log
```

## Live Smoke Checks

Confirm the rendered path rules inside the running container:

```bash
ssh -o BatchMode=yes -l root -i ~/.ansible/ssh/proxmox_lxc servarr.faviann.vms \
  'cd /conf/docker/stacks/beets-flask && docker compose exec -T beets-flask sh -lc "sed -n '\''/^paths:/,/^[^[:space:]\"-]/p'\'' /config/beets/config.yaml"'
```

Confirm the plugin imports:

```bash
ssh -o BatchMode=yes -l root -i ~/.ansible/ssh/proxmox_lxc servarr.faviann.vms \
  'cd /conf/docker/stacks/beets-flask && docker compose exec -T beets-flask python -c "import beetsplug.VGMplug"'
```

Confirm the service is reachable on the published port and through the protected Traefik route before using it for imports.
