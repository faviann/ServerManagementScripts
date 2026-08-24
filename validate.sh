#!/bin/bash
# Deterministic, non-live repository validation.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

uv run --locked ansible-lint
uv run --locked python tests/regression/run_lxc_lifecycle_regressions.py --full
uv run --locked pytest
