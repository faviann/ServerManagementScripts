"""Behavioral tests for daemon-options execution-proof reporting."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


LAUNCHER_PATH = (
    Path(__file__).resolve().parents[1]
    / "regression"
    / "test_lxc_docker_runtime_daemon_options.py"
)
SPEC = importlib.util.spec_from_file_location("daemon_options_regression", LAUNCHER_PATH)
assert SPEC is not None and SPEC.loader is not None
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def completed(stdout: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["ansible-playbook"],
        returncode=0,
        stdout=json.dumps(stdout) if not isinstance(stdout, str) else stdout,
        stderr="",
    )


def report_with(
    observation: str | None = None, outcome: dict[str, object] | None = None
) -> dict[str, object]:
    tasks = []
    if observation is not None:
        tasks.append(
            {
                "task": {"name": observation},
                "hosts": {"localhost": outcome or {}},
            }
        )
    return {"plays": [{"tasks": tasks}]}


def test_structurally_incomplete_observation_results_fail_closed() -> None:
    report = {
        "plays": [
            {
                "tasks": [
                    {"task": {"name": name}, "hosts": {"localhost": {}}}
                    for name in launcher.REQUIRED_OBSERVATIONS
                ]
            }
        ]
    }

    with pytest.raises(AssertionError, match="execution proof failed"):
        launcher.assert_observations_completed(completed(report))


@pytest.mark.parametrize(
    "outcome", [{"skipped": True}, {"failed": True}, {"unreachable": True}]
)
def test_nonpassing_observation_names_the_observation(
    outcome: dict[str, object],
) -> None:
    observation = launcher.REQUIRED_OBSERVATIONS[0]

    with pytest.raises(AssertionError, match=observation):
        launcher.assert_observations_completed(
            completed(report_with(observation, outcome))
        )


def test_absent_observation_names_the_observation() -> None:
    observation = launcher.REQUIRED_OBSERVATIONS[0]

    with pytest.raises(AssertionError, match=observation):
        launcher.assert_observations_completed(completed(report_with()))


@pytest.mark.parametrize("stdout", ["not json", [], {}])
def test_non_machine_readable_report_fails_closed(stdout: object) -> None:
    with pytest.raises(AssertionError, match="pinned JSON callback report"):
        launcher.assert_observations_completed(completed(stdout))
