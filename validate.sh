#!/bin/bash
# Deterministic, non-live repository validation.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

usage() {
    cat <<'EOF'
Usage: ./validate.sh [operation] [options]

With no operation, run the comprehensive non-live handoff validation:
repo-wide lint, the full lifecycle regression set, and the whole test suite,
stopping at the first failure.

Operations:
  lint                        Run repo-wide production-profile lint only
  lifecycle [--full] [--only <launcher.py>]... [--fail-fast]
                              Run the lifecycle regression set
  tests [<target>...]         Run the test suite, optionally restricted to
                              targets inside tests/ (a path, optionally with a
                              ::node-id suffix)
  stack <path>                Validate one repo-managed stack update policy;
                              schema-versioned JSON on stdout, diagnostics on
                              stderr

Options:
  --full                      Run the full lifecycle regression set
  --only <launcher.py>        Run only this registered launcher (repeatable)
  --fail-fast                 Stop the lifecycle set after the first failure
  --help                      Show this help
EOF
}

usage_error() {
    echo "validate.sh: $1" >&2
    echo "Try './validate.sh --help' for usage." >&2
    exit 2
}

operation="handoff"
case "${1:-}" in
    --help)
        usage
        exit 0
        ;;
    "") ;;
    lint | lifecycle | tests | stack)
        operation="$1"
        shift
        ;;
    -*)
        usage_error "unknown option"
        ;;
    *)
        usage_error "unknown operation"
        ;;
esac

lifecycle_arguments=()
lifecycle_full=false
lifecycle_only=false
test_targets=()
stack_paths=()

in_test_tree() {
    local resolved
    resolved="$(realpath -m -- "$1" 2>/dev/null)" || return 1
    [[ "$resolved" == "$PROJECT_ROOT/tests" ]] ||
        [[ "$resolved" == "$PROJECT_ROOT/tests/"* ]]
}

lifecycle_option() {
    [[ "$operation" == "lifecycle" ]] || usage_error "unknown option"
}

while (($#)); do
    case "$1" in
        --help)
            usage
            exit 0
            ;;
        --full)
            lifecycle_option
            lifecycle_full=true
            lifecycle_arguments+=("--full")
            shift
            ;;
        --fail-fast)
            lifecycle_option
            lifecycle_arguments+=("--fail-fast")
            shift
            ;;
        --only)
            lifecycle_option
            (($# >= 2)) && [[ -n "$2" ]] && [[ "$2" != -* ]] ||
                usage_error "$1 requires a value"
            lifecycle_only=true
            lifecycle_arguments+=("--only" "$2")
            shift 2
            ;;
        -*)
            usage_error "unknown option"
            ;;
        *)
            case "$operation" in
                tests)
                    in_test_tree "${1%%::*}" ||
                        usage_error "test target outside tests/: $1"
                    test_targets+=("$1")
                    ;;
                stack)
                    stack_paths+=("$1")
                    ;;
                *)
                    usage_error "$operation takes no arguments"
                    ;;
            esac
            shift
            ;;
    esac
done

if [[ "$operation" == "lifecycle" ]] && $lifecycle_full && $lifecycle_only; then
    usage_error "--only cannot be combined with --full"
fi
if [[ "$operation" == "stack" ]] && ((${#stack_paths[@]} != 1)); then
    usage_error "stack requires exactly one stack path"
fi

# Keep every validation child on repository-owned, credential-free Ansible
# inputs. Individual regression launchers inherit the same boundary.
export ANSIBLE_INVENTORY="$PROJECT_ROOT/tests/fixtures/ansible/inventory.yml"
export ANSIBLE_VAULT_PASSWORD_FILE="$PROJECT_ROOT/tests/fixtures/ansible/vault-pass"

# Isolate non-live validation from the operator's live fact cache (issue #89).
VALIDATION_CACHE_DIR="$(mktemp -d)"
trap 'rm -rf -- "$VALIDATION_CACHE_DIR"' EXIT
export ANSIBLE_CACHE_PLUGIN_CONNECTION="$VALIDATION_CACHE_DIR"

case "$operation" in
    handoff)
        uv run --locked ansible-lint
        uv run --locked python \
            tests/regression/run_lxc_lifecycle_regressions.py --full
        uv run --locked pytest
        ;;
    lint)
        uv run --locked ansible-lint
        ;;
    lifecycle)
        uv run --locked python \
            tests/regression/run_lxc_lifecycle_regressions.py \
            ${lifecycle_arguments[@]+"${lifecycle_arguments[@]}"}
        ;;
    tests)
        uv run --locked pytest ${test_targets[@]+"${test_targets[@]}"}
        ;;
    stack)
        uv run --locked python -m stack_update_policy validate \
            --repository-root "$PROJECT_ROOT" "${stack_paths[0]}"
        ;;
esac
