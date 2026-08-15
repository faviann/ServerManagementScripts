"""Execution-boundary tests for the lifecycle regression runner."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import ModuleType

import pytest


RUNNER_PATH = (
    Path(__file__).resolve().parents[1]
    / "regression"
    / "run_lxc_lifecycle_regressions.py"
)


def load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "lxc_lifecycle_regression_runner", RUNNER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def passing_result(script: str) -> tuple[str, int, float, str]:
    return script, 0, 0.0, ""


def test_only_selects_registered_launchers_in_supplied_order() -> None:
    runner = load_runner()
    launched: list[str] = []

    def launch(script: str) -> tuple[str, int, float, str]:
        launched.append(script)
        return passing_result(script)

    selected = [runner.FULL_ONLY_SCRIPTS[0], runner.FAST_SCRIPTS[1]]

    assert runner.main(
        [argument for script in selected for argument in ("--only", script)],
        launcher=launch,
    ) == 0
    assert launched == selected


def test_nvidia_repository_launcher_is_registered_once_as_full_only() -> None:
    runner = load_runner()
    target = "test_lxc_nvidia_runtime_repository.py"
    launched: list[str] = []

    assert runner.FULL_ONLY_SCRIPTS.count(target) == 1
    assert target not in runner.FAST_SCRIPTS
    assert runner.REGISTERED_SCRIPTS.count(target) == 1

    assert runner.main(
        ["--only", target],
        launcher=lambda script: launched.append(script) or passing_result(script),
    ) == 0
    assert launched == [target]


def test_only_and_full_are_rejected_before_launch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = load_runner()
    launched: list[str] = []

    with pytest.raises(SystemExit) as error:
        runner.main(
            ["--full", "--only", runner.FAST_SCRIPTS[0]],
            launcher=lambda script: launched.append(script) or passing_result(script),
        )

    assert error.value.code == 2
    assert launched == []
    assert "--only cannot be combined with --full" in capsys.readouterr().err


def test_unknown_launcher_is_rejected_with_registered_names(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = load_runner()
    launched: list[str] = []

    with pytest.raises(SystemExit) as error:
        runner.main(
            ["--only", "missing.py"],
            launcher=lambda script: launched.append(script) or passing_result(script),
        )

    stderr = capsys.readouterr().err
    assert error.value.code == 2
    assert launched == []
    assert "unknown lifecycle launcher: missing.py" in stderr
    assert runner.FAST_SCRIPTS[0] in stderr
    assert runner.FULL_ONLY_SCRIPTS[-1] in stderr


def test_full_fail_fast_finishes_running_fast_launchers_without_starting_full_only() -> None:
    runner = load_runner()
    launched: list[str] = []

    def launch(script: str) -> tuple[str, int, float, str]:
        launched.append(script)
        return script, int(script == runner.FAST_SCRIPTS[0]), 0.0, "failure"

    assert runner.main(["--full", "--fail-fast"], launcher=launch) == 1
    assert set(launched) == set(runner.FAST_SCRIPTS)
    assert not set(launched).intersection(runner.FULL_ONLY_SCRIPTS)


def test_full_fail_fast_stops_sequential_phase_after_first_failure() -> None:
    runner = load_runner()
    launched: list[str] = []
    failing_script = runner.FULL_ONLY_SCRIPTS[1]

    def launch(script: str) -> tuple[str, int, float, str]:
        launched.append(script)
        return script, int(script == failing_script), 0.0, "failure"

    assert runner.main(["--full", "--fail-fast"], launcher=launch) == 1
    assert set(launched[: len(runner.FAST_SCRIPTS)]) == set(runner.FAST_SCRIPTS)
    assert launched[len(runner.FAST_SCRIPTS) :] == list(
        runner.FULL_ONLY_SCRIPTS[:2]
    )


def test_targeted_fail_fast_stops_after_first_failure() -> None:
    runner = load_runner()
    selected = list(runner.REGISTERED_SCRIPTS[:3])
    launched: list[str] = []

    def launch(script: str) -> tuple[str, int, float, str]:
        launched.append(script)
        return script, int(script == selected[1]), 0.0, "failure"

    arguments = [
        argument for script in selected for argument in ("--only", script)
    ]
    assert runner.main([*arguments, "--fail-fast"], launcher=launch) == 1
    assert launched == selected[:2]


def test_default_fast_path_starts_both_launchers_concurrently() -> None:
    runner = load_runner()
    both_started = threading.Barrier(len(runner.FAST_SCRIPTS))
    launched: list[str] = []

    def launch(script: str) -> tuple[str, int, float, str]:
        launched.append(script)
        both_started.wait(timeout=2)
        return passing_result(script)

    assert runner.main([], launcher=launch) == 0
    assert set(launched) == set(runner.FAST_SCRIPTS)


def test_full_path_without_fail_fast_aggregates_all_launcher_results(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = load_runner()
    launched: list[str] = []
    failing_scripts = {
        runner.FAST_SCRIPTS[0],
        runner.FULL_ONLY_SCRIPTS[1],
    }

    def launch(script: str) -> tuple[str, int, float, str]:
        launched.append(script)
        return script, int(script in failing_scripts), 0.0, "failure"

    assert runner.main(["--full"], launcher=launch) == 1
    assert set(launched[: len(runner.FAST_SCRIPTS)]) == set(runner.FAST_SCRIPTS)
    assert launched[len(runner.FAST_SCRIPTS) :] == list(runner.FULL_ONLY_SCRIPTS)
    assert (
        f"failed: {runner.FAST_SCRIPTS[0]}, {runner.FULL_ONLY_SCRIPTS[1]}"
        in capsys.readouterr().err
    )


def test_targeted_launchers_use_and_restore_isolated_fixture_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = load_runner()
    original_vault = "/credential-that-must-not-be-read"
    original_inventory = "/inventory-that-must-not-be-read"
    monkeypatch.setenv("ANSIBLE_VAULT_PASSWORD_FILE", original_vault)
    monkeypatch.setenv("ANSIBLE_INVENTORY", original_inventory)

    def launch(script: str) -> tuple[str, int, float, str]:
        vault_path = Path(os.environ["ANSIBLE_VAULT_PASSWORD_FILE"])
        inventory_path = Path(os.environ["ANSIBLE_INVENTORY"])
        assert vault_path.read_text(encoding="utf-8") == "unused-fixture-placeholder\n"
        assert inventory_path.read_text(encoding="utf-8") == (
            "[local]\nlocalhost ansible_connection=local\n"
        )
        return passing_result(script)

    assert runner.main(
        ["--only", runner.FULL_ONLY_SCRIPTS[0]], launcher=launch
    ) == 0
    assert os.environ["ANSIBLE_VAULT_PASSWORD_FILE"] == original_vault
    assert os.environ["ANSIBLE_INVENTORY"] == original_inventory


def test_targeted_runner_smoke_uses_isolated_ansible_environment() -> None:
    target = "test_lxc_spec_invalid_guest_bootstrap.py"
    environment = os.environ.copy()
    environment.update(
        {
            "ANSIBLE_VAULT_PASSWORD_FILE": "/credential-that-must-not-be-read",
            "ANSIBLE_INVENTORY": "/inventory-that-must-not-be-read",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
        }
    )

    result = subprocess.run(
        [sys.executable, str(RUNNER_PATH), "--only", target],
        cwd=RUNNER_PATH.parents[2],
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert f"PASS  {target}" in result.stdout
    assert "ok: targeted lifecycle regression set passed (1 launchers)" in result.stdout
