#!/bin/bash
# Read-only managed-host diagnostics.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$PROJECT_ROOT/scripts/lib/live-execution.sh"

usage() {
    cat <<'EOF'
Usage: ./inspect.sh <operation> [options]

Operations:
  credentials                 Validate Proxmox API credentials and permissions
  connectivity [--limit <targets>]
                              Check managed-LXC SSH connectivity
  containers                  List every LXC on the Proxmox node
  plan [--limit <targets>]    Report lifecycle planning problems without execution
  vars <host> | vars --graph  Show merged variables masked, or the inventory graph

Options:
  --limit <targets>           Select hosts with Ansible limit grammar
  --help                      Show this help
EOF
}

usage_error() {
    echo "inspect.sh: $1" >&2
    echo "Try './inspect.sh --help' for usage." >&2
    exit 2
}

requires_check_mode=false
case "${1:-}" in
    --help)
        usage
        exit 0
        ;;
    credentials)
        playbook="playbooks/validate-credentials.yml"
        prerequisite_layers="control-node"
        supports_limit=false
        shift
        ;;
    connectivity)
        playbook="playbooks/lab-connectivity.yml"
        prerequisite_layers="control-node"
        supports_limit=true
        shift
        ;;
    containers)
        playbook="playbooks/proxmox_api_check.yml"
        prerequisite_layers="control-node"
        supports_limit=false
        shift
        ;;
    plan)
        playbook="playbooks/validate-infrastructure.yml"
        prerequisite_layers="control-node,proxmox-host"
        supports_limit=true
        requires_check_mode=true
        shift
        ;;
    vars)
        shift
        (($# == 1)) || usage_error "vars requires one host or --graph"
        if [[ "$1" == "--graph" ]]; then
            if uv run --locked ansible-inventory \
                -i inventory/hosts.yml --graph 2>/dev/null; then
                exit 0
            fi
            exit 1
        fi
        [[ "$1" != -* ]] || usage_error "unknown option"
        if uv run --locked ansible-inventory \
            -i inventory/hosts.yml --host "$1" --yaml 2>/dev/null \
            | uv run --locked python -m scripts.masked_inventory; then
            exit 0
        fi
        exit 1
        ;;
    "")
        usage_error "an operation is required"
        ;;
    *)
        usage_error "unknown operation"
        ;;
esac

limit_pattern=""
while (($#)); do
    case "$1" in
        --limit)
            $supports_limit || usage_error "--limit is not valid for this operation"
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
    prerequisite_target_pattern="localhost"
fi
$requires_check_mode && arguments+=("--check")
arguments+=("-e" "prerequisite_target_pattern=$prerequisite_target_pattern")

run_live_playbook shared "$prerequisite_layers" "$playbook" "${arguments[@]}"
