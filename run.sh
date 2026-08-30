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

inspect_passthrough_short_options() {
    local short_options="${1#-}"
    local short_option
    passthrough_consumes_next=false
    while [[ -n "$short_options" ]]; do
        short_option="${short_options:0:1}"
        short_options="${short_options:1}"
        case "$short_option" in
            C|i|l|t)
                return 1
                ;;
            B|M|P|T|c|e|f|u)
                [[ -n "$short_options" ]] || passthrough_consumes_next=true
                return 0
                ;;
        esac
    done
    return 0
}

playbook="site.yml"
lifecycle_intent="full"
case "${1:-}" in
    full)
        shift
        ;;
    provision)
        playbook="playbooks/provision-lxcs.yml"
        lifecycle_intent="provision_only"
        shift
        ;;
    configure)
        playbook="playbooks/configure-lxcs.yml"
        lifecycle_intent="configure_only"
        shift
        ;;
    --help)
        usage
        exit 0
        ;;
    ""|-*)
        ;;
    *)
        usage_error "unknown operation"
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
            usage_error "unknown option"
            ;;
    esac
done

for ((index = 0; index < ${#passthrough[@]}; index++)); do
    argument="${passthrough[index]}"
    case "$argument" in
        --lim*|--ch*|--tag*|--sk*|--inv*|--sta*)
            usage_error \
                "passthrough cannot set target selection, lifecycle intent, or check mode"
            ;;
        -e|--extra-vars)
            ((index + 1 < ${#passthrough[@]})) || usage_error "$argument requires a value"
            ((index += 1))
            ;;
        --extra-vars=*|-e?*)
            ;;
        --*)
            ;;
        -?*)
            if ! inspect_passthrough_short_options "$argument"; then
                usage_error \
                    "passthrough cannot set target selection, lifecycle intent, or check mode"
            fi
            if $passthrough_consumes_next; then
                ((index + 1 < ${#passthrough[@]})) || usage_error "short option requires a value"
                ((index += 1))
            fi
            ;;
    esac
done

arguments=("${verbosity[@]}" "${passthrough[@]}")
if $include_controller; then
    arguments+=("--limit" "workstation")
elif [[ -n "$limit_pattern" ]]; then
    arguments+=("--limit" "$limit_pattern")
fi
if $check_mode; then
    arguments+=("--check")
fi
arguments+=("-e" "proxmox_lifecycle_intent=$lifecycle_intent")
if $include_controller; then
    arguments+=("-e" "proxmox_skip_self=false")
else
    arguments+=("-e" "proxmox_skip_self=true")
fi
if [[ -n "$stack_name" ]]; then
    arguments+=("-e" "stack_filter=$stack_name")
else
    arguments+=('--extra-vars={"stack_filter":null}')
fi
run_live_playbook "$lock_class" control-node,proxmox-host "$playbook" "${arguments[@]}"
