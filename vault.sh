#!/bin/bash
# Transactional, non-disclosing vault operations.

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_FILE="$PROJECT_ROOT/inventory/group_vars/all/vault.yml"
PASS_FILE="${ANSIBLE_VAULT_PASSWORD_FILE:-$HOME/.ansible/vault-pass}"
TRANSACTION_WORKSPACE=""
TRANSACTION_PUBLISH_TMP=""

usage() {
    cat <<'EOF'
Usage: ./vault.sh <operation>

Operations:
  configure  Create or update Proxmox API credentials
  edit       Edit the complete encrypted vault
  check      Verify the vault without disclosing its contents
EOF
}

check_line() {
    local name="$1"
    local result="$2"
    printf '%s: %s\n' "$name" "$result"
    [[ "$result" == PASS ]] || CHECK_FAILED=1
}

report_content_failures() {
    check_line "YAML mapping" FAIL
    check_line "vault_proxmox_api_user" FAIL
    check_line "vault_proxmox_api_token_id" FAIL
    check_line "vault_proxmox_api_token_secret" FAIL
}

check_vault() {
    local workspace plaintext content_checks mode owner
    CHECK_FAILED=0

    if [[ -f "$VAULT_FILE" ]] && IFS= read -r first_line < "$VAULT_FILE" \
        && [[ "$first_line" =~ ^\$ANSIBLE_VAULT\;1\.[12]\;AES256($|\;) ]]; then
        check_line "vault header" PASS
    else
        check_line "vault header" FAIL
    fi

    if [[ -f "$PASS_FILE" ]]; then
        check_line "passphrase file" PASS
    else
        check_line "passphrase file" FAIL
    fi
    if [[ -s "$PASS_FILE" ]]; then
        check_line "passphrase nonempty" PASS
    else
        check_line "passphrase nonempty" FAIL
    fi
    owner="$(stat -c %u -- "$PASS_FILE" 2>/dev/null || true)"
    if [[ -n "$owner" && "$owner" == "$(id -u)" ]]; then
        check_line "passphrase ownership" PASS
    else
        check_line "passphrase ownership" FAIL
    fi
    mode="$(stat -c %a -- "$PASS_FILE" 2>/dev/null || true)"
    if [[ -n "$mode" ]] && (( (8#$mode & 077) == 0 )); then
        check_line "passphrase permissions" PASS
    else
        check_line "passphrase permissions" FAIL
    fi

    workspace="$(mktemp -d /dev/shm/homelab-vault.XXXXXX)" || {
        check_line "decryptability" FAIL
        report_content_failures
        return 1
    }
    chmod 700 "$workspace"
    plaintext="$workspace/vault.yml"
    if uv run --locked ansible-vault view "$VAULT_FILE" >"$plaintext" 2>/dev/null; then
        check_line "decryptability" PASS
        content_checks="$workspace/checks"
        if uv run --locked python - "$plaintext" >"$content_checks" 2>/dev/null <<'PY'
import sys
from pathlib import Path

import yaml

try:
    value = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, yaml.YAMLError):
    value = None

is_mapping = isinstance(value, dict)
print(f"YAML mapping|{'PASS' if is_mapping else 'FAIL'}")
for key in (
    "vault_proxmox_api_user",
    "vault_proxmox_api_token_id",
    "vault_proxmox_api_token_secret",
):
    item = value.get(key) if is_mapping else None
    classification = item.strip() if isinstance(item, str) else None
    valid = (
        isinstance(item, str)
        and bool(item.strip())
        and classification not in ("REPLACE_ME", "<REPLACE_ME>")
        and not classification.startswith("REPLACE_WITH_")
    )
    print(f"{key}|{'PASS' if valid else 'FAIL'}")
PY
        then
            while IFS='|' read -r name result; do
                check_line "$name" "$result"
            done <"$content_checks"
        else
            report_content_failures
        fi
    else
        check_line "decryptability" FAIL
        report_content_failures
    fi
    rm -rf -- "$workspace"
    (( CHECK_FAILED == 0 ))
}

open_tty() {
    [[ -t 0 && -t 1 ]] || return 1
    exec 3<>/dev/tty
}

confirm_plaintext_conversion() {
    local answer
    printf 'Vault is unencrypted. Encrypt and replace it? [y/N] ' >&3
    IFS= read -r answer <&3 || return 1
    [[ "$answer" == y || "$answer" == Y || "$answer" == yes || "$answer" == YES ]]
}

required_credential_valid() {
    local value="$1"
    local classification
    classification="${value#"${value%%[![:space:]]*}"}"
    classification="${classification%"${classification##*[![:space:]]}"}"
    [[ "$value" =~ [^[:space:]] ]] \
        && [[ "$classification" != "REPLACE_ME" ]] \
        && [[ "$classification" != "<REPLACE_ME>" ]] \
        && [[ "$classification" != REPLACE_WITH_* ]]
}

stage_existing_vault() {
    local plaintext="$1"
    if [[ ! -e "$VAULT_FILE" ]]; then
        printf '%s\n' '---' >"$plaintext"
    elif IFS= read -r first_line <"$VAULT_FILE" \
        && [[ "$first_line" == '$ANSIBLE_VAULT;'* ]]; then
        if ! uv run --locked ansible-vault decrypt \
            --output "$plaintext" "$VAULT_FILE" >/dev/null 2>&1; then
            return 1
        fi
    else
        confirm_plaintext_conversion || return 1
        cp -- "$VAULT_FILE" "$plaintext"
    fi
    chmod 600 "$plaintext"
}

cleanup_transaction() {
    local target="$TRANSACTION_WORKSPACE"
    local status=0
    [[ -n "$target" ]] || return 0
    [[ "$(dirname "$target")" == /dev/shm \
        && "$(basename "$target")" == homelab-vault.* ]] || return 1
    rm -rf -- "$target" || status=1
    if [[ ! -e "$target" ]]; then
        TRANSACTION_WORKSPACE=""
    else
        status=1
    fi
    return "$status"
}

discard_pending_ciphertext() {
    local target="$TRANSACTION_PUBLISH_TMP"
    local status=0
    [[ -n "$target" ]] || return 0
    [[ "$(dirname "$target")" == "$(dirname "$VAULT_FILE")" \
        && "$(basename "$target")" == vault.yml.tmp.* ]] || return 1
    rm -f -- "$target" || status=1
    if [[ ! -e "$target" ]]; then
        TRANSACTION_PUBLISH_TMP=""
    else
        status=1
    fi
    return "$status"
}

disarm_transaction_cleanup() {
    if [[ -z "$TRANSACTION_WORKSPACE" && -z "$TRANSACTION_PUBLISH_TMP" ]]; then
        trap - EXIT HUP INT TERM
    fi
}

cleanup_transaction_on_exit() {
    local status=$?
    trap - EXIT HUP INT TERM
    cleanup_transaction || status=1
    discard_pending_ciphertext || status=1
    exit "$status"
}

interrupt_transaction() {
    exit 1
}

validate_plaintext() {
    uv run --locked python - "$1" >/dev/null 2>&1 <<'PY'
import sys
from pathlib import Path

import yaml

try:
    value = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, yaml.YAMLError):
    raise SystemExit(1)
raise SystemExit(0 if isinstance(value, dict) else 1)
PY
}

stage_ciphertext_for_publication() {
    local plaintext="$1"
    local encrypted="$2"

    validate_plaintext "$plaintext" || return 1
    cp -- "$plaintext" "$encrypted" || return 1
    chmod 600 "$encrypted"
    uv run --locked ansible-vault encrypt "$encrypted" >/dev/null 2>&1 || return 1
    uv run --locked ansible-vault view "$encrypted" >/dev/null 2>&1 || return 1

    mkdir -p "$(dirname "$VAULT_FILE")"
    TRANSACTION_PUBLISH_TMP="$(mktemp "${VAULT_FILE}.tmp.XXXXXX")" || return 1
    if ! install -m 600 -- "$encrypted" "$TRANSACTION_PUBLISH_TMP"; then
        discard_pending_ciphertext || true
        return 1
    fi
}

run_mutation() {
    local operation="$1"
    local workspace plaintext encrypted credentials editor_command
    workspace="$(mktemp -d /dev/shm/homelab-vault.XXXXXX)" || return 1
    TRANSACTION_WORKSPACE="$workspace"
    TRANSACTION_PUBLISH_TMP=""
    trap cleanup_transaction_on_exit EXIT
    trap interrupt_transaction HUP INT TERM
    chmod 700 "$workspace"
    plaintext="$workspace/vault.yml"
    encrypted="$workspace/vault.encrypted"

    if ! stage_existing_vault "$plaintext"; then
        cleanup_transaction || true
        return 1
    fi

    if [[ "$operation" == configure ]]; then
        local api_user token_id token_secret
        printf 'Proxmox API user: ' >&3
        IFS= read -r api_user <&3 || { cleanup_transaction || true; return 1; }
        printf 'Proxmox API token ID: ' >&3
        IFS= read -r token_id <&3 || { cleanup_transaction || true; return 1; }
        printf 'Proxmox API token secret: ' >&3
        IFS= read -rs token_secret <&3 || { cleanup_transaction || true; return 1; }
        printf '\n' >&3
        if ! required_credential_valid "$api_user" \
            || ! required_credential_valid "$token_id" \
            || ! required_credential_valid "$token_secret"; then
            cleanup_transaction || true
            return 1
        fi
        credentials="$workspace/credentials"
        printf '%s\n%s\n%s\n' "$api_user" "$token_id" "$token_secret" >"$credentials"
        chmod 600 "$credentials"
        unset api_user token_id token_secret
        if ! uv run --locked python - "$plaintext" "$credentials" >/dev/null 2>&1 <<'PY'
import sys
from pathlib import Path

import yaml

vault_path = Path(sys.argv[1])
credentials = Path(sys.argv[2]).read_text(encoding="utf-8").splitlines()
try:
    value = yaml.safe_load(vault_path.read_text(encoding="utf-8"))
except yaml.YAMLError:
    raise SystemExit(1)
if value is None:
    value = {}
if not isinstance(value, dict) or len(credentials) != 3:
    raise SystemExit(1)
for key, credential in zip(
    (
        "vault_proxmox_api_user",
        "vault_proxmox_api_token_id",
        "vault_proxmox_api_token_secret",
    ),
    credentials,
    strict=True,
):
    value[key] = credential
vault_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
PY
        then
            cleanup_transaction || true
            return 1
        fi
    else
        editor_command="${VISUAL:-${EDITOR:-vi}}"
        local -a editor_argv
        read -r -a editor_argv <<<"$editor_command"
        if (( ${#editor_argv[@]} == 0 )) || ! "${editor_argv[@]}" "$plaintext"; then
            cleanup_transaction || true
            return 1
        fi
    fi

    if stage_ciphertext_for_publication "$plaintext" "$encrypted"; then
        if ! cleanup_transaction; then
            discard_pending_ciphertext || true
            return 1
        fi
        if mv -f -- "$TRANSACTION_PUBLISH_TMP" "$VAULT_FILE"; then
            TRANSACTION_PUBLISH_TMP=""
            disarm_transaction_cleanup
            printf '%s: PASS\n' "$operation"
            return 0
        fi
        discard_pending_ciphertext || true
        disarm_transaction_cleanup
    else
        cleanup_transaction || true
        discard_pending_ciphertext || true
        disarm_transaction_cleanup
    fi
    printf '%s: FAIL\n' "$operation" >&2
    return 1
}

if (( $# == 0 )); then
    usage >&2
    exit 2
fi

case "$1" in
    --help|-h)
        (( $# == 1 )) || exit 2
        usage
        exit 0
        ;;
    check)
        (( $# == 1 )) || exit 2
        check_vault
        exit $?
        ;;
    configure|edit)
        (( $# == 1 )) || exit 2
        open_tty || {
            printf '%s: FAIL\n' "$1" >&2
            exit 1
        }
        run_mutation "$1"
        exit $?
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
