#!/bin/bash
# Locked entry point for full and sliced LXC lifecycle runs.

set -euo pipefail

CALLER_WORKING_DIRECTORY="$(pwd -P)"
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

validate_passthrough_extra_vars() {
    local assignment="$1"
    local inspection_status=0
    local file_path
    local resolved_file_path
    validated_extra_vars_encoding="$assignment"
    if [[ "$assignment" == @* ]]; then
        file_path="${assignment#@}"
        if [[ "$file_path" != /* ]]; then
            file_path="$CALLER_WORKING_DIRECTORY/$file_path"
        fi
        resolved_file_path="$(realpath -e -- "$file_path" 2>/dev/null)" || \
            usage_error "passthrough extra-vars file cannot be resolved"
        validated_extra_vars_encoding="@$resolved_file_path"
    fi
    (
        cd "$PROJECT_ROOT"
        uv run --locked python -c '
import sys
from pathlib import Path

import yaml
from ansible.parsing.splitter import parse_kv

encoding = sys.argv[1]
try:
    if encoding.startswith("@"):
        data = yaml.safe_load(Path(encoding[1:]).read_text(encoding="utf-8"))
    elif encoding.startswith(("{", "[")):
        data = yaml.safe_load(encoding)
    else:
        data = parse_kv(encoding)
    if not isinstance(data, dict) or "_raw_params" in data:
        raise ValueError("extra vars do not resolve to an inspectable mapping")
except Exception:
    raise SystemExit(11)

protected = {"proxmox_lifecycle_intent", "stack_filter", "proxmox_skip_self"}
raise SystemExit(10 if protected.intersection(data) else 0)
' "$validated_extra_vars_encoding"
    ) || inspection_status=$?
    case "$inspection_status" in
        0)
            ;;
        10)
            usage_error "passthrough cannot override a wrapper-owned variable"
            ;;
        *)
            usage_error "passthrough extra vars must have inspectable effective keys"
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
            validate_passthrough_extra_vars "${passthrough[index + 1]}"
            passthrough[index + 1]="$validated_extra_vars_encoding"
            ;;
        --extra-vars=*)
            validate_passthrough_extra_vars "${argument#*=}"
            passthrough[index]="--extra-vars=$validated_extra_vars_encoding"
            ;;
        -e*)
            validate_passthrough_extra_vars "${argument#-e}"
            passthrough[index]="-e$validated_extra_vars_encoding"
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
