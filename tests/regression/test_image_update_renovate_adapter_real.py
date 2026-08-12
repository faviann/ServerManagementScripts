from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_contract_fixtures_cross_the_real_pinned_renovate_boundary() -> None:
    credential_free_environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("RENOVATE_", "NPM_CONFIG_"))
        and key not in {"NPM_TOKEN", "NODE_AUTH_TOKEN", "DOCKER_AUTH_CONFIG", "YARN_NPM_AUTH_TOKEN"}
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "image_update_renovate_adapter.py"),
            str(ROOT / "tests" / "fixtures" / "image_update_renovate_adapter" / "contract-request.json"),
        ],
        cwd=ROOT,
        env=credential_free_environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["provenance"]["renovate_version"] == "44.5.0"
    assert [(item["stack"], item["observation_count"]) for item in result["stacks"]] == [
        ("major-line-with-major", 1),
        ("floating-multiarch", 1),
        ("coordinated", 2),
        ("isolated-failure", 1),
        ("vendor-baseline", 1),
    ]
    assert next(item for item in result["stacks"] if item["stack"] == "isolated-failure")["scan_status"] == "incomplete"
    floating = next(item for item in result["observations"] if item["identity"]["stack"] == "floating-multiarch")
    assert floating["candidate"]["proposed_exact_reference"].startswith("alpine:3.20@sha256:")
    vendor = next(item for item in result["observations"] if item["identity"]["stack"] == "vendor-baseline")
    assert vendor["candidate"]["effective_reference"] == "ghcr.io/immich-app/immich-server:v2.7.5"
    assert vendor["candidate"]["proposed_exact_reference"] == "ghcr.io/immich-app/immich-server:v2.7.5"
    assert vendor["candidate"]["update_type"] in {"minor", "patch"}
    assert vendor["visible_major_alternatives"]
    major_line = next(item for item in result["observations"] if item["identity"]["stack"] == "major-line-with-major")
    assert major_line["candidate"]["update_type"] in {"minor", "patch"}
    assert major_line["candidate"]["effective_reference"] == major_line["candidate"]["proposed_exact_reference"]
    assert "@" not in major_line["candidate"]["proposed_exact_reference"]
    assert major_line["candidate"]["digest"].startswith("sha256:")
    assert major_line["visible_major_alternatives"]
