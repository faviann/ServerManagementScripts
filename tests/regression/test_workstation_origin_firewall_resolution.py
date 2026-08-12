#!/usr/bin/env python3
"""Regression test for workstation origin firewall host resolution behavior."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "tests" / "regression" / "fixtures" / "workstation_origin_firewall_resolution_failure.yml"
ANSIBLE_PLAYBOOK = "uv run --locked ansible-playbook".split()


def run_playbook(playbook: Path, temp_root: str) -> subprocess.CompletedProcess[str]:
    command = [*ANSIBLE_PLAYBOOK, str(playbook), "-f", "1", "-e", f"temp_root={temp_root}"]
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def test_workstation_origin_firewall_fails_when_allowed_host_has_no_ipv4_resolution() -> None:
    with tempfile.TemporaryDirectory(prefix="workstation-origin-firewall-resolution-failure-") as temp_root:
        result = run_playbook(PLAYBOOK, temp_root)

    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0, output
    assert "could not resolve any IPv4 address for" in output, output
    assert "broken-portal" in output, output
    assert "unresolvable-hostname.invalid" in output, output
