#!/usr/bin/env python3
"""Regression coverage for the non-live validation command boundary."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "validate.sh"
VALIDATION_COMMANDS = [
    ["run", "--locked", "ansible-lint"],
    [
        "run",
        "--locked",
        "python",
        "tests/regression/run_lxc_lifecycle_regressions.py",
        "--full",
    ],
    ["run", "--locked", "pytest"],
]


def validation_environment(tmp_path: Path, *, fail_at: int = 0) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

capture = Path(os.environ["VALIDATE_TEST_CAPTURE"])
commands = []
if capture.exists():
    commands = json.loads(capture.read_text(encoding="utf-8"))
commands.append({
    "argv": sys.argv[1:],
    "cache_connection": os.environ.get("ANSIBLE_CACHE_PLUGIN_CONNECTION"),
    "inventory": os.environ.get("ANSIBLE_INVENTORY"),
    "lifecycle_marker": os.environ.get("HOMELAB_IAC_LIFECYCLE_WRAPPER"),
    "vault_password_file": os.environ.get("ANSIBLE_VAULT_PASSWORD_FILE"),
})
capture.write_text(json.dumps(commands), encoding="utf-8")
if len(commands) == int(os.environ.get("VALIDATE_TEST_FAIL_AT", "0")):
    raise SystemExit(41)
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env.pop("HOMELAB_IAC_LIFECYCLE_WRAPPER", None)
    env.update(
        {
            "ANSIBLE_INVENTORY": "/operator/inventory-that-must-not-be-read",
            "ANSIBLE_VAULT_PASSWORD_FILE": (
                "/operator/vault-password-that-must-not-be-read"
            ),
            "HOME": str(tmp_path / "home"),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "VALIDATE_TEST_CAPTURE": str(tmp_path / "commands.json"),
            "VALIDATE_TEST_FAIL_AT": str(fail_at),
        }
    )
    return env


def run_validation(env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RUNNER)],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def captured_commands(tmp_path: Path) -> list[dict[str, object]]:
    return json.loads((tmp_path / "commands.json").read_text(encoding="utf-8"))


def assert_validation_cache_was_shared_then_removed(
    commands: list[dict[str, object]],
) -> None:
    cache_connections = {entry["cache_connection"] for entry in commands}
    assert len(cache_connections) == 1
    cache_connection = cache_connections.pop()
    assert isinstance(cache_connection, str)
    assert not Path(cache_connection).exists()


def test_validate_runs_the_complete_non_live_suite_from_the_repository_root(
    tmp_path: Path,
) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, tmp_path)

    assert result.returncode == 0, result.stderr
    commands = captured_commands(tmp_path)
    assert commands == [
        {
            "argv": command,
            "cache_connection": commands[0]["cache_connection"],
            "inventory": str(REPO_ROOT / "tests/fixtures/ansible/inventory.yml"),
            "lifecycle_marker": None,
            "vault_password_file": str(
                REPO_ROOT / "tests/fixtures/ansible/vault-pass"
            ),
        }
        for command in VALIDATION_COMMANDS
    ]
    assert_validation_cache_was_shared_then_removed(commands)
    assert not (Path(env["HOME"]) / ".ansible/homelab-iac-lifecycle.lock").exists()


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_validate_propagates_a_gate_failure_and_stops(
    tmp_path: Path, fail_at: int
) -> None:
    env = validation_environment(tmp_path, fail_at=fail_at)

    result = run_validation(env, REPO_ROOT)

    assert result.returncode == 41
    commands = captured_commands(tmp_path)
    assert [entry["argv"] for entry in commands] == VALIDATION_COMMANDS[:fail_at]
    assert_validation_cache_was_shared_then_removed(commands)
