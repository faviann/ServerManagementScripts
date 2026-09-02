#!/usr/bin/env python3
"""Credential-free convergence regression for the workstation origin firewall."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from ansible_test_helper import ansible_playbook_command


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests" / "regression" / "fixtures"
PLAYBOOK = FIXTURE_ROOT / "workstation_origin_firewall_idempotency.yml"
INVENTORY = FIXTURE_ROOT / "workstation_origin_firewall_inventory.yml"
STUB_ROOT = FIXTURE_ROOT / "workstation_origin_firewall_assets"
ANSIBLE_PLAYBOOK = ansible_playbook_command(supplies_own_inventory=True)


def run_playbook(temp_root: str, stub_state: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{STUB_ROOT / 'bin'}:{environment['PATH']}"
    environment["WORKSTATION_ORIGIN_FIREWALL_STUB_STATE"] = stub_state
    command = [
        "unshare",
        "-Ur",
        *ANSIBLE_PLAYBOOK,
        "-i",
        str(INVENTORY),
        str(PLAYBOOK),
        "-f",
        "1",
        "-e",
        f"temp_root={temp_root}",
    ]
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_workstation_origin_firewall_second_convergence_is_idempotent() -> None:
    with tempfile.TemporaryDirectory(prefix="workstation-origin-firewall-idempotency-") as temp_root:
        temp_path = Path(temp_root)
        stub_state_path = temp_path / "stub-state"
        stub_state_path.mkdir()
        (stub_state_path / "legacy-table").touch()
        legacy_service = temp_path / "etc/systemd/system/workstation-aoe-proxy-firewall.service"
        legacy_rules = temp_path / "etc/nftables.d/workstation-aoe-proxy.nft"
        legacy_service.parent.mkdir(parents=True)
        legacy_rules.parent.mkdir(parents=True)
        legacy_service.write_text("legacy service\n", encoding="utf-8")
        legacy_rules.write_text("legacy rules\n", encoding="utf-8")

        stub_state = str(stub_state_path)
        first = run_playbook(temp_root, stub_state)

        first_output = f"{first.stdout}\n{first.stderr}"
        assert first.returncode == 0, first_output
        assert "failed=0" in first_output, first_output
        assert "Restart workstation origin firewall" in first_output, first_output
        assert (stub_state_path / "apt").exists(), first_output
        assert (stub_state_path / "systemd").exists(), first_output
        assert (stub_state_path / "validated-rules").exists(), first_output
        assert not (stub_state_path / "legacy-table").exists(), first_output
        assert not legacy_service.exists(), first_output
        assert not legacy_rules.exists(), first_output

        nft_path = temp_path / "etc/nftables.d/custom-origin.nft"
        service_path = temp_path / "etc/systemd/system/custom-origin-firewall.service"
        assert nft_path.is_file(), first_output
        assert service_path.is_file(), first_output
        assert "elements = { 4001, 9119, 18789, 8788 }" in nft_path.read_text(encoding="utf-8")
        assert f"ExecStart=/usr/sbin/nft -f {nft_path}" in service_path.read_text(encoding="utf-8")

        second = run_playbook(temp_root, stub_state)

    second_output = f"{second.stdout}\n{second.stderr}"
    assert second.returncode == 0, second_output
    assert "changed=0" in second_output, second_output
    assert "failed=0" in second_output, second_output
