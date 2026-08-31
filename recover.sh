#!/bin/bash
# Locked entry point for Manual SSH recovery.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$PROJECT_ROOT/scripts/lib/live-execution.sh"

usage() {
    cat <<'EOF'
Usage: ./recover.sh <operation> [options]

Operations:
  ssh-keys [--limit <targets>]
                              Restore control-node SSH access to existing LXCs

Options:
  --limit <targets>           Select hosts with Ansible limit grammar
  --help                      Show this help
EOF
}

usage_error() {
    echo "recover.sh: $1" >&2
    echo "Try './recover.sh --help' for usage." >&2
    exit 2
}

case "${1:-}" in
    --help)
        usage
        exit 0
        ;;
    "")
        usage_error "an operation is required"
        ;;
    ssh-keys)
        shift
        ;;
    *)
        usage_error "unknown operation"
        ;;
esac

limit_pattern=""
while (($#)); do
    case "$1" in
        --limit)
            (($# >= 2)) && [[ -n "$2" ]] && [[ "$2" != -* ]] || \
                usage_error "$1 requires a value"
            limit_pattern="$2"
            shift 2
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            usage_error "unknown option"
            ;;
    esac
done

arguments=()
if [[ -n "$limit_pattern" ]]; then
    arguments+=("--limit" "$limit_pattern")
    prerequisite_target_pattern="$limit_pattern"
else
    prerequisite_target_pattern="lxcs"
fi
arguments+=("-e" "prerequisite_target_pattern=$prerequisite_target_pattern")

run_live_playbook \
    exclusive \
    control-node,proxmox-host \
    playbooks/add-ssh-keys-to-lxcs.yml \
    "${arguments[@]}"
