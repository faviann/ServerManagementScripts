#!/bin/bash
# Locked entry point for full and sliced LXC lifecycle runs.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$PROJECT_ROOT/scripts/lib/live-execution.sh"

usage() {
    cat <<'EOF'
Usage: ./run.sh [full|provision|configure] [options] [-- ansible-arguments]

Operations:
  full       Run the full lifecycle (default)
  provision  Run the provision-only lifecycle
  configure  Run the configure-only lifecycle

Options:
  --limit <targets>       Select hosts with Ansible limit grammar
  --check                 Run in check mode
  --stack <name>          Configure only the named stack
  --include-controller    Include the control node
  -v, -vv, -vvv           Set Ansible verbosity
  --help                  Show this help
EOF
}

usage_error() {
    echo "run.sh: $1" >&2
    echo "Try './run.sh --help' for usage." >&2
    exit 2
}

reject_protected_extra_vars() {
    local assignment="$1"
    if [[ ! "$assignment" =~ ^[a-zA-Z_][a-zA-Z0-9_]*=[^[:space:]]*$ ]]; then
        usage_error "passthrough extra vars must use an inspectable key=value encoding"
    fi
    case "${assignment%%=*}" in
        proxmox_lifecycle_intent|stack_filter|proxmox_skip_self)
            usage_error "passthrough cannot override a wrapper-owned variable"
            ;;
    esac
}

playbook="site.yml"
case "${1:-}" in
    full)
        shift
        ;;
    provision)
        playbook="playbooks/provision-lxcs.yml"
        shift
        ;;
    configure)
        playbook="playbooks/configure-lxcs.yml"
        shift
        ;;
    --help)
        usage
        exit 0
        ;;
    ""|-*)
        ;;
    *)
        usage_error "unknown operation: $1"
        ;;
esac

passthrough=()
lock_class="exclusive"
limit_pattern=""
stack_name=""
include_controller=false
check_mode=false
verbosity=()
while (($#)); do
    case "$1" in
        --limit)
            (($# >= 2)) && [[ "$2" != -* ]] || usage_error "$1 requires a value"
            limit_pattern="$2"
            shift 2
            ;;
        --stack)
            (($# >= 2)) && [[ "$2" != -* ]] || usage_error "$1 requires a value"
            stack_name="$2"
            shift 2
            ;;
        --include-controller)
            include_controller=true
            shift
            ;;
        --check)
            lock_class="shared"
            check_mode=true
            shift
            ;;
        -v|-vv|-vvv)
            verbosity+=("$1")
            shift
            ;;
        --help)
            usage
            exit 0
            ;;
        --)
            shift
            passthrough=("$@")
            break
            ;;
        *)
            usage_error "unknown option: $1"
            ;;
    esac
done

for ((index = 0; index < ${#passthrough[@]}; index++)); do
    argument="${passthrough[index]}"
    case "$argument" in
        --lim*|-l|-l*|--ch*|-C|\
        --tag*|-t|-t*|--sk*|\
        --inv*|-i|-i*|--sta*)
            usage_error \
                "passthrough cannot set target selection, lifecycle intent, or check mode: $argument"
            ;;
        -e|--extra-vars)
            ((index + 1 < ${#passthrough[@]})) || usage_error "$argument requires a value"
            reject_protected_extra_vars "${passthrough[index + 1]}"
            ;;
        --extra-vars=*)
            reject_protected_extra_vars "${argument#*=}"
            ;;
        -e*)
            reject_protected_extra_vars "${argument#-e}"
            ;;
    esac
done

arguments=("${verbosity[@]}")
if $include_controller; then
    arguments+=("--limit" "workstation" "-e" "proxmox_skip_self=false")
elif [[ -n "$limit_pattern" ]]; then
    arguments+=("--limit" "$limit_pattern")
fi
if $check_mode; then
    arguments+=("--check")
fi
if [[ -n "$stack_name" ]]; then
    arguments+=("-e" "stack_filter=$stack_name")
fi
arguments+=("${passthrough[@]}")
run_live_playbook "$lock_class" control-node,proxmox-host "$playbook" "${arguments[@]}"
