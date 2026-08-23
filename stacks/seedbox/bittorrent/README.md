# BitTorrent Stack

This stack runs qBittorrent through Gluetun on the `seedbox` Docker host. qBittorrent and `ws-ephemeral` intentionally share Gluetun's network namespace, so reachable ports are published on `gluetun`, not on the `qbittorrent` service.

## Normalization Boundary

This stack intentionally does not follow every ordinary app-stack default.

Preserve:

- Do not move qBittorrent Web UI or torrenting ports from `gluetun` to `qbittorrent`.
- Do not remove `network_mode: service:gluetun`.
- Do not treat `qbittorrent` route labels without local service ports as a blocker.
- Do not change helper services that share the VPN namespace to use Docker service DNS instead of `127.0.0.1` namespace-local ports.

Do not use this stack as a template for normal application stacks.

## Windscribe Endpoint Selection

The current Gluetun server hostnames were chosen from Windscribe 10 Gbps locations:

1. Check Windscribe server status: `https://windscribe.com/status/`.
2. Generate a WireGuard config from `https://windscribe.com/getconfig/wireguard`.
3. Use the generated WireGuard hostname to look up matching Gluetun `servers.json` hostnames.
4. Add the selected hostnames to `SERVER_HOSTNAMES` in `compose.yaml`.

Previously considered Montreal Expo 67 hostnames:

- `ca-050.whiskergalaxy.com`
- `ca-051.whiskergalaxy.com`
- `ca-052.whiskergalaxy.com`
- `ca-053.whiskergalaxy.com`

Current New York hostnames:

- `us-east-116.whiskergalaxy.com`
- `us-east-120.whiskergalaxy.com`

## Ownership

Stack-owned:

- `compose.yaml`
- `.env.j2`
- this `README.md`
- Gluetun, qBittorrent, and `ws-ephemeral` stack wiring

Host-owned:

- WireGuard feature support through `cap_wireguard`
- `/data` and `/ephemeral` mounts
- seedbox vault-backed variables in `inventory/host_vars/seedbox.yml`

## Deploy

```bash
./run.sh --limit seedbox -e stack_filter=bittorrent
```
