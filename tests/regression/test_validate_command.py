#!/usr/bin/env python3
"""Grammar and boundary coverage for the non-live validation command."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "validate.sh"
FIXTURE_INVENTORY = str(REPO_ROOT / "tests/fixtures/ansible/inventory.yml")
FIXTURE_VAULT_PASSWORD_FILE = str(REPO_ROOT / "tests/fixtures/ansible/vault-pass")
OPERATOR_MARKER = "operator-secret-marker-4f2b"


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
            "ANSIBLE_INVENTORY": operator_sentinel(tmp_path, "inventory"),
            "ANSIBLE_VAULT_PASSWORD_FILE": operator_sentinel(tmp_path, "vault-pass"),
            "HOME": str(tmp_path / "home"),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "VALIDATE_TEST_CAPTURE": str(tmp_path / "commands.json"),
            "VALIDATE_TEST_FAIL_AT": str(fail_at),
        }
    )
    return env


def operator_sentinel(tmp_path: Path, name: str) -> str:
    """An operator input the command must neither read nor disclose."""
    sentinel = tmp_path / f"operator-{name}"
    sentinel.write_text(f"{OPERATOR_MARKER}\n", encoding="utf-8")
    sentinel.chmod(0o000)
    return str(sentinel)


def run_validation(
    env: dict[str, str],
    cwd: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RUNNER), *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def captured_commands(tmp_path: Path) -> list[dict[str, object]]:
    capture = tmp_path / "commands.json"
    if not capture.exists():
        return []
    return json.loads(capture.read_text(encoding="utf-8"))


def child_kind(argv: list[str]) -> str:
    if "ansible-lint" in argv:
        return "lint"
    if any(item.endswith("run_lxc_lifecycle_regressions.py") for item in argv):
        return "lifecycle"
    if "stack_update_policy" in argv:
        return "stack"
    if "pytest" in argv:
        return "tests"
    return f"unrecognized:{' '.join(argv)}"


def child_kinds(commands: list[dict[str, object]]) -> list[str]:
    return [child_kind(entry["argv"]) for entry in commands]


def child_options(argv: list[str], marker: str) -> list[str]:
    """The arguments the command forwarded after the child it selected."""
    for index, item in enumerate(argv):
        if item == marker or item.endswith(marker):
            return argv[index + 1 :]
    raise AssertionError(f"{marker} missing from {argv}")


def assert_validation_cache_was_shared_then_removed(
    commands: list[dict[str, object]],
) -> None:
    cache_connections = {entry["cache_connection"] for entry in commands}
    assert len(cache_connections) == 1
    cache_connection = cache_connections.pop()
    assert isinstance(cache_connection, str)
    assert not Path(cache_connection).exists()


def assert_fixture_environment(commands: list[dict[str, object]]) -> None:
    assert commands
    for entry in commands:
        assert entry["inventory"] == FIXTURE_INVENTORY
        assert entry["vault_password_file"] == FIXTURE_VAULT_PASSWORD_FILE
        assert entry["lifecycle_marker"] is None
    assert_validation_cache_was_shared_then_removed(commands)


# --- AC1 / AC6: the no-argument comprehensive handoff run -------------------


def test_no_argument_run_is_the_comprehensive_non_live_handoff_validation(
    tmp_path: Path,
) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, tmp_path)

    assert result.returncode == 0, result.stderr
    commands = captured_commands(tmp_path)
    assert child_kinds(commands) == ["lint", "lifecycle", "tests"]
    assert child_options(commands[1]["argv"], "run_lxc_lifecycle_regressions.py") == [
        "--full"
    ]
    assert_fixture_environment(commands)
    assert not (Path(env["HOME"]) / ".ansible/homelab-iac-lifecycle.lock").exists()


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_no_argument_run_propagates_a_gate_failure_and_stops(
    tmp_path: Path, fail_at: int
) -> None:
    env = validation_environment(tmp_path, fail_at=fail_at)

    result = run_validation(env, REPO_ROOT)

    assert result.returncode == 41
    commands = captured_commands(tmp_path)
    assert child_kinds(commands) == ["lint", "lifecycle", "tests"][:fail_at]
    assert_validation_cache_was_shared_then_removed(commands)


def test_no_argument_run_does_not_validate_stack_update_policy(
    tmp_path: Path,
) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT)

    assert result.returncode == 0, result.stderr
    assert "stack" not in child_kinds(captured_commands(tmp_path))


# --- AC8: grammar and exit convention --------------------------------------


def test_help_exits_zero_and_names_every_operation(tmp_path: Path) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, "--help")

    assert result.returncode == 0, result.stderr
    output = f"{result.stdout}\n{result.stderr}"
    for operation in ("lint", "lifecycle", "tests", "stack"):
        assert operation in output
    assert captured_commands(tmp_path) == []


@pytest.mark.parametrize(
    "arguments",
    [
        ("bogus",),
        ("--bogus",),
        ("lint", "--bogus"),
        ("lifecycle", "--bogus"),
        ("tests", "--bogus"),
        ("stack", "--bogus"),
    ],
)
def test_unknown_operation_or_option_is_invalid_usage(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, *arguments)

    assert result.returncode == 2, result.stdout
    assert captured_commands(tmp_path) == []


# --- AC2 / AC3 / AC4: the targeted feedback operations ----------------------


def test_lint_runs_repo_wide_lint_alone(tmp_path: Path) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, "lint")

    assert result.returncode == 0, result.stderr
    commands = captured_commands(tmp_path)
    assert child_kinds(commands) == ["lint"]
    assert child_options(commands[0]["argv"], "ansible-lint") == []


def test_lint_rejects_a_path_argument(tmp_path: Path) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, "lint", "playbooks")

    assert result.returncode == 2, result.stdout
    assert captured_commands(tmp_path) == []


def test_lifecycle_runs_the_fast_path_by_default(tmp_path: Path) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, "lifecycle")

    assert result.returncode == 0, result.stderr
    commands = captured_commands(tmp_path)
    assert child_kinds(commands) == ["lifecycle"]
    assert child_options(commands[0]["argv"], "run_lxc_lifecycle_regressions.py") == []


@pytest.mark.parametrize(
    ("arguments", "forwarded"),
    [
        (("--full",), ["--full"]),
        (
            ("--only", "test_lxc_lifecycle_decision.py"),
            ["--only", "test_lxc_lifecycle_decision.py"],
        ),
        (
            (
                "--only",
                "test_lxc_lifecycle_decision.py",
                "--only",
                "test_lifecycle_run_lock.py",
            ),
            [
                "--only",
                "test_lxc_lifecycle_decision.py",
                "--only",
                "test_lifecycle_run_lock.py",
            ],
        ),
        (("--fail-fast",), ["--fail-fast"]),
        (("--full", "--fail-fast"), ["--full", "--fail-fast"]),
    ],
)
def test_lifecycle_forwards_every_supported_selection(
    tmp_path: Path, arguments: tuple[str, ...], forwarded: list[str]
) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, "lifecycle", *arguments)

    assert result.returncode == 0, result.stderr
    commands = captured_commands(tmp_path)
    assert child_kinds(commands) == ["lifecycle"]
    assert (
        child_options(commands[0]["argv"], "run_lxc_lifecycle_regressions.py")
        == forwarded
    )


def test_lifecycle_rejects_only_combined_with_full(tmp_path: Path) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(
        env,
        REPO_ROOT,
        "lifecycle",
        "--full",
        "--only",
        "test_lxc_lifecycle_decision.py",
    )

    assert result.returncode == 2, result.stdout
    assert captured_commands(tmp_path) == []


def test_tests_runs_the_whole_suite_without_a_target(tmp_path: Path) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, "tests")

    assert result.returncode == 0, result.stderr
    commands = captured_commands(tmp_path)
    assert child_kinds(commands) == ["tests"]
    assert child_options(commands[0]["argv"], "pytest") == []


@pytest.mark.parametrize(
    "target",
    [
        "tests/regression/test_validate_command.py",
        "tests/regression/test_validate_command.py"
        "::test_lint_runs_repo_wide_lint_alone",
    ],
)
def test_tests_forwards_an_in_tree_target(tmp_path: Path, target: str) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, "tests", target)

    assert result.returncode == 0, result.stderr
    commands = captured_commands(tmp_path)
    assert child_kinds(commands) == ["tests"]
    assert child_options(commands[0]["argv"], "pytest") == [target]


@pytest.mark.parametrize("target", ["validate.sh", "../outside/test_x.py"])
def test_tests_rejects_a_target_outside_the_test_tree(
    tmp_path: Path, target: str
) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, "tests", target)

    assert result.returncode == 2, result.stdout
    assert captured_commands(tmp_path) == []


@pytest.mark.parametrize(
    "paths",
    [(), ("stacks/workstation/mcp-auth-proxy", "stacks/workstation/another")],
)
def test_stack_requires_exactly_one_stack_path(
    tmp_path: Path, paths: tuple[str, ...]
) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, "stack", *paths)

    assert result.returncode == 2, result.stdout
    assert captured_commands(tmp_path) == []


def test_stack_validates_exactly_the_named_stack(tmp_path: Path) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(
        env, REPO_ROOT, "stack", "stacks/workstation/mcp-auth-proxy"
    )

    assert result.returncode == 0, result.stderr
    commands = captured_commands(tmp_path)
    assert child_kinds(commands) == ["stack"]
    assert commands[0]["argv"][-1] == "stacks/workstation/mcp-auth-proxy"


@pytest.mark.parametrize(
    "arguments",
    [
        ("lint", "--bogus:--help"),
        ("lifecycle", "--fail-fast:--help"),
        ("tests", "validate.sh:--help"),
        ("tests", "/etc/passwd:--help"),
    ],
)
def test_a_help_suffixed_argument_is_still_judged_by_its_own_branch(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, *arguments)

    assert result.returncode == 2, result.stdout
    assert captured_commands(tmp_path) == []


def test_a_help_suffixed_stack_path_stays_a_stack_path(tmp_path: Path) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, "stack", "stacks/a/b:--help")

    assert result.returncode == 0, result.stderr
    commands = captured_commands(tmp_path)
    assert child_kinds(commands) == ["stack"]
    assert commands[0]["argv"][-1] == "stacks/a/b:--help"


@pytest.mark.parametrize(
    "operation", ["lint", "lifecycle", "tests", "stack"]
)
def test_help_after_any_operation_exits_zero(
    tmp_path: Path, operation: str
) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, operation, "--help")

    assert result.returncode == 0, result.stderr
    assert "lifecycle" in f"{result.stdout}\n{result.stderr}"
    assert captured_commands(tmp_path) == []


@pytest.mark.parametrize("target", ["", "::test_foo"])
def test_an_unusable_test_target_reports_the_commands_own_usage_error(
    tmp_path: Path, target: str
) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, "tests", target)

    assert result.returncode == 2, result.stdout
    assert result.stderr.startswith("validate.sh: test target outside tests/")
    assert "realpath" not in result.stderr
    assert captured_commands(tmp_path) == []


# --- AC5: the stack update policy machine interface ------------------------


def run_real_validation(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RUNNER), *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=120,
    )


def test_stack_reports_a_valid_policy_as_schema_versioned_json() -> None:
    result = run_real_validation("stack", "stacks/workstation/mcp-auth-proxy")

    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["schema_version"] == 1
    assert document["valid"] is True
    assert "stacks/workstation/mcp-auth-proxy" in result.stderr


def test_stack_reports_an_invalid_contract_on_stderr_and_exits_non_zero() -> None:
    result = run_real_validation(
        "stack", "stacks/workstation/no-such-stack-for-validation"
    )

    assert result.returncode != 0
    document = json.loads(result.stdout)
    assert document["schema_version"] == 1
    assert document["valid"] is False
    assert "missing-stack" in json.dumps(document)
    assert "selected stack directory does not exist" in result.stderr
    assert not result.stdout.startswith("identity:")


# --- AC7 / AC10: the fixture environment and agent safety ------------------


OPERATION_ENTRY_POINTS = [
    (),
    ("lint",),
    ("lifecycle",),
    ("tests",),
    ("stack", "stacks/workstation/mcp-auth-proxy"),
]


@pytest.mark.parametrize("arguments", OPERATION_ENTRY_POINTS)
def test_every_operation_runs_under_the_fixture_environment(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, *arguments)

    assert result.returncode == 0, result.stderr
    assert_fixture_environment(captured_commands(tmp_path))


@pytest.mark.parametrize("arguments", OPERATION_ENTRY_POINTS)
def test_every_operation_is_agent_safe(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    env = validation_environment(tmp_path)

    result = run_validation(env, REPO_ROOT, *arguments)

    assert result.returncode == 0, result.stderr
    assert OPERATOR_MARKER not in result.stdout
    assert OPERATOR_MARKER not in result.stderr
    for name in ("ANSIBLE_INVENTORY", "ANSIBLE_VAULT_PASSWORD_FILE"):
        sentinel = Path(env[name])
        assert sentinel.stat().st_mode & 0o777 == 0
    for kind in child_kinds(captured_commands(tmp_path)):
        assert kind in {"lint", "lifecycle", "tests", "stack"}


# --- AC9: grammar coverage replaces the exact-argv pinning -----------------


def test_this_module_covers_the_grammar_instead_of_pinning_a_full_child_argv() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    retired_pinning = "VALIDATION_" + "COMMANDS"

    assert retired_pinning not in source
    for grammar_test in (
        "test_no_argument_run_is_the_comprehensive_non_live_handoff_validation",
        "test_no_argument_run_propagates_a_gate_failure_and_stops",
        "test_no_argument_run_does_not_validate_stack_update_policy",
        "test_help_exits_zero_and_names_every_operation",
        "test_unknown_operation_or_option_is_invalid_usage",
        "test_lifecycle_forwards_every_supported_selection",
        "test_lifecycle_rejects_only_combined_with_full",
        "test_tests_rejects_a_target_outside_the_test_tree",
        "test_stack_requires_exactly_one_stack_path",
        "test_every_operation_runs_under_the_fixture_environment",
        "test_every_operation_is_agent_safe",
    ):
        assert grammar_test in globals()
