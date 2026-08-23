#!/bin/bash
# Serialized entry point for lifecycle-mutating LXC runs.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly LIFECYCLE_LOCK_FILE="${HOME}/.ansible/homelab-iac-lifecycle.lock"
readonly LIFECYCLE_WRAPPER_MARKER="HOMELAB_IAC_LIFECYCLE_WRAPPER"

playbook="site.yml"
case "${1:-}" in
    provision)
        playbook="playbooks/provision-lxcs.yml"
        shift
        ;;
    configure)
        playbook="playbooks/configure-lxcs.yml"
        shift
        ;;
esac

mkdir -p "$(dirname "$LIFECYCLE_LOCK_FILE")"
exec {lifecycle_lock_fd}>>"$LIFECYCLE_LOCK_FILE"
if ! flock --exclusive --nonblock "$lifecycle_lock_fd"; then
    echo "Another machine-local lifecycle run holds $LIFECYCLE_LOCK_FILE:" >&2
    sed -n '1p' "$LIFECYCLE_LOCK_FILE" >&2
    exit 75
fi
printf 'pid=%s worktree=%s\n' "$$" "$PROJECT_ROOT" >"$LIFECYCLE_LOCK_FILE"

export "$LIFECYCLE_WRAPPER_MARKER=1"
cd "$PROJECT_ROOT"

echo "Running Ansible playbook through uv: $playbook $*"
echo "────────────────────────────────────────"
if uv run --locked ansible-playbook "$playbook" "$@"; then
    exit 0
else
    exit $?
fi
