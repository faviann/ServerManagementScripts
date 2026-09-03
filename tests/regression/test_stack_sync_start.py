#!/usr/bin/env python3
"""Test the complete stack-sync start behavior."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from ansible_test_helper import ansible_playbook_command

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "tests" / "regression" / "fixtures" / "stack_sync_start_test.yml"
ANSIBLE_PLAYBOOK = ansible_playbook_command()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="stack-sync-start-") as temp_root:
        proc = subprocess.run(
            [*ANSIBLE_PLAYBOOK, str(PLAYBOOK), "-e", f"temp_root={temp_root}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )

    output = f"{proc.stdout}\n{proc.stderr}"

    if proc.returncode != 0:
        print("playbook failed unexpectedly", file=sys.stderr)
        print(output, file=sys.stderr)
        return 1

    print("ok: stack sync start behavior")
    return 0


def test_stack_sync_start_combined_behavior() -> None:
    assert main() == 0


def test_stack_sync_start_subject_invokes_playbook_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def successful_playbook(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", successful_playbook)

    test_stack_sync_start_combined_behavior()

    assert len(calls) == 1


def test_stack_sync_start_failure_exposes_playbook_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def failed_playbook(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="stack-sync-start-stdout-sentinel",
            stderr="stack-sync-start-stderr-sentinel",
        )

    monkeypatch.setattr(subprocess, "run", failed_playbook)

    with pytest.raises(AssertionError):
        test_stack_sync_start_combined_behavior()

    captured = capsys.readouterr()
    assert "stack-sync-start-stdout-sentinel" in captured.err
    assert "stack-sync-start-stderr-sentinel" in captured.err


if __name__ == "__main__":
    raise SystemExit(main())
