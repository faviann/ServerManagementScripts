#!/bin/bash
# Shared live-playbook execution boundary. Callers own their command grammar.

readonly LIVE_EXECUTION_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly LIVE_EXECUTION_LOCK_FILE="${HOME}/.ansible/homelab-iac-lifecycle.lock"
readonly LIVE_EXECUTION_HOLDER_DIR="${LIVE_EXECUTION_LOCK_FILE}.holders"
readonly LIVE_EXECUTION_WRAPPER_MARKER="HOMELAB_IAC_LIFECYCLE_WRAPPER"

write_live_holder_record() {
    local holder_file="$1"
    local holder_pid="$2"
    local temporary_record="${holder_file}.tmp"
    printf 'pid=%s worktree=%s\n' \
        "$holder_pid" "$LIVE_EXECUTION_PROJECT_ROOT" >"$temporary_record"
    mv "$temporary_record" "$holder_file"
}

live_holder_is_active() {
    local holder_pid="$1"
    local descriptor
    local descriptor_target
    [[ "$holder_pid" =~ ^[0-9]+$ ]] && [[ -d "/proc/$holder_pid/fd" ]] || return 1
    for descriptor in "/proc/$holder_pid/fd/"*; do
        descriptor_target="$(readlink "$descriptor" 2>/dev/null)" || continue
        if [[ "$descriptor_target" == "$LIVE_EXECUTION_LOCK_FILE" ]]; then
            return 0
        fi
    done
    return 1
}

report_live_lock_holder() {
    local holder_file
    local holder_record
    local holder_pid
    for holder_file in "$LIVE_EXECUTION_HOLDER_DIR/"*.holder; do
        [[ -f "$holder_file" ]] || continue
        holder_record="$(sed -n '1p' "$holder_file")"
        holder_pid="${holder_record%% *}"
        holder_pid="${holder_pid#pid=}"
        if live_holder_is_active "$holder_pid"; then
            echo "$holder_record" >&2
            return
        fi
    done
    sed -n '1p' "$LIVE_EXECUTION_LOCK_FILE" >&2
}

run_live_playbook() {
    local lock_class="$1"
    local prerequisite_layers="$2"
    local playbook="$3"
    shift 3

    case "$prerequisite_layers:$playbook" in
        control-node,proxmox-host:site.yml|\
        control-node,proxmox-host:playbooks/provision-lxcs.yml|\
        control-node,proxmox-host:playbooks/configure-lxcs.yml)
            ;;
        *)
            echo \
                "Unsupported prerequisite layers '$prerequisite_layers' for live playbook '$playbook'" \
                >&2
            return 2
            ;;
    esac

    mkdir -p "$(dirname "$LIVE_EXECUTION_LOCK_FILE")" "$LIVE_EXECUTION_HOLDER_DIR"
    exec {live_execution_lock_fd}>>"$LIVE_EXECUTION_LOCK_FILE"
    case "$lock_class" in
        shared)
            if ! flock --shared --nonblock "$live_execution_lock_fd"; then
                echo "Another machine-local live operation holds $LIVE_EXECUTION_LOCK_FILE:" >&2
                report_live_lock_holder
                exec {live_execution_lock_fd}>&-
                return 75
            fi
            ;;
        exclusive)
            if ! flock --exclusive --nonblock "$live_execution_lock_fd"; then
                echo "Another machine-local live operation holds $LIVE_EXECUTION_LOCK_FILE:" >&2
                report_live_lock_holder
                exec {live_execution_lock_fd}>&-
                return 75
            fi
            ;;
        *)
            echo "Unsupported live lock class: $lock_class" >&2
            exec {live_execution_lock_fd}>&-
            return 2
            ;;
    esac

    local holder_file="${LIVE_EXECUTION_HOLDER_DIR}/$$.holder"
    write_live_holder_record "$holder_file" "$$"
    export "$LIVE_EXECUTION_WRAPPER_MARKER=1"
    cd "$LIVE_EXECUTION_PROJECT_ROOT"

    echo "Running Ansible playbook through uv: $playbook $*"
    echo "────────────────────────────────────────"
    local status=0
    (
        write_live_holder_record "$holder_file" "$BASHPID"
        exec uv run --locked ansible-playbook "$playbook" "$@"
    ) || status=$?
    rm -f -- "$holder_file"
    exec {live_execution_lock_fd}>&-
    return "$status"
}
