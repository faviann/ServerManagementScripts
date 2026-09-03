"""Contract tests for the shared Ansible regression-test invocation helper."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import socket
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests/fixtures/ansible"
ANSIBLE_PLAYBOOK_EXECUTABLE = "ansible-playbook"
RAW_LOCKED_PLAYBOOK = ("uv", "run", "--locked", ANSIBLE_PLAYBOOK_EXECUTABLE)
RAW_LOCKED_PLAYBOOK_TEXT = " ".join(RAW_LOCKED_PLAYBOOK)
RAW_REPEATED_SPACE_PLAYBOOK_TEXT = "  ".join(RAW_LOCKED_PLAYBOOK)
RAW_TABBED_PLAYBOOK_TEXT = "\t".join(RAW_LOCKED_PLAYBOOK)
RAW_MULTILINE_PLAYBOOK_TEXT = "\n".join(RAW_LOCKED_PLAYBOOK)


def load_helper() -> ModuleType:
    helper_path = Path(__file__).with_name("ansible_test_helper.py")
    spec = importlib.util.spec_from_file_location("ansible_test_helper", helper_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def set_fixture_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSIBLE_INVENTORY", str(FIXTURE_ROOT / "inventory.yml"))
    monkeypatch.setenv(
        "ANSIBLE_VAULT_PASSWORD_FILE", str(FIXTURE_ROOT / "vault-pass")
    )


def assert_no_raw_playbook_literals(paths: list[Path]) -> None:
    non_python_separator = r"(?:[\s,\[\]'\"]|-(?=\s))+"
    non_python_literal = non_python_separator.join(
        re.escape(word) for word in RAW_LOCKED_PLAYBOOK
    )
    python_string_literal = r"\s+".join(
        re.escape(word) for word in RAW_LOCKED_PLAYBOOK
    )

    def represents_raw_argv(node: ast.AST) -> bool:
        if isinstance(node, (ast.List, ast.Tuple)):
            words = tuple(
                element.value
                for element in node.elts[: len(RAW_LOCKED_PLAYBOOK)]
                if isinstance(element, ast.Constant)
                and isinstance(element.value, str)
            )
            return words == RAW_LOCKED_PLAYBOOK
        return False

    def represents_raw_string(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.search(python_string_literal, node.value) is not None
        )

    offenders = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        if path.suffix != ".py":
            if re.search(non_python_literal, source):
                offenders.append(path)
            continue

        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if represents_raw_argv(node):
                offenders.append(path)
                break
            if represents_raw_string(node):
                offenders.append(path)
                break

    assert not offenders, (
        "replace raw locked playbook literals with ansible_playbook_command: "
        + ", ".join(str(path) for path in offenders)
    )


def test_constructs_locked_playbook_invocation_with_fixture_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_fixture_environment(monkeypatch)
    helper = load_helper()

    assert helper.ansible_playbook_command("fixture.yml", "--check") == [
        *RAW_LOCKED_PLAYBOOK,
        "fixture.yml",
        "--check",
    ]


@pytest.mark.parametrize(
    ("relative_path", "source"),
    [
        (
            "ansible_test_helper.py",
            f'ANSIBLE_PLAYBOOK = "{RAW_LOCKED_PLAYBOOK_TEXT}".split()\n',
        ),
        (
            "test_list_invocation.py",
            'subprocess.run(["uv", "run", "--locked", "ansible-playbook"])\n',
        ),
        (
            "test_tuple_invocation.py",
            'subprocess.run(("uv", "run", "--locked", "ansible-playbook"))\n',
        ),
        (
            "test_string_invocation.py",
            f'subprocess.run("{RAW_LOCKED_PLAYBOOK_TEXT} fixture.yml", shell=True)\n',
        ),
        (
            "test_assignment.py",
            'COMMAND = ["uv", "run", "--locked", "ansible-playbook"]\n',
        ),
        (
            "test_annotated_assignment.py",
            'COMMAND: tuple[str, ...] = ("uv", "run", "--locked", "ansible-playbook")\n',
        ),
        (
            "test_named_expression.py",
            f'if command := "{RAW_LOCKED_PLAYBOOK_TEXT} fixture.yml":\n    pass\n',
        ),
        (
            "test_keyword_invocation.py",
            'execute(command=["uv", "run", "--locked", "ansible-playbook"])\n',
        ),
        (
            "test_unlisted_invocation.py",
            'arbitrary_callable(("uv", "run", "--locked", "ansible-playbook"))\n',
        ),
        (
            "test_return.py",
            'def command():\n    return ["uv", "run", "--locked", "ansible-playbook"]\n',
        ),
        (
            "test_positional_default.py",
            'def invoke(command=("uv", "run", "--locked", "ansible-playbook")):\n    pass\n',
        ),
        (
            "test_keyword_default.py",
            'def invoke(*, command=["uv", "run", "--locked", "ansible-playbook"]):\n    pass\n',
        ),
        (
            "test_lambda_default.py",
            'invoke = lambda command=("uv", "run", "--locked", "ansible-playbook"): command\n',
        ),
        (
            "test_nested_literal.py",
            'WRAPPED = [["uv", "run", "--locked", "ansible-playbook"]]\n',
        ),
        (
            "test_return_string.py",
            f'def command():\n    return "{RAW_LOCKED_PLAYBOOK_TEXT}"\n',
        ),
        (
            "test_positional_string_default.py",
            f'def invoke(command="{RAW_LOCKED_PLAYBOOK_TEXT}"):\n    pass\n',
        ),
        (
            "test_keyword_string_default.py",
            f'def invoke(*, command="{RAW_LOCKED_PLAYBOOK_TEXT}"):\n    pass\n',
        ),
        (
            "test_lambda_string_default.py",
            f'invoke = lambda command="{RAW_LOCKED_PLAYBOOK_TEXT}": command\n',
        ),
        (
            "test_nested_string.py",
            f'WRAPPED = [["{RAW_LOCKED_PLAYBOOK_TEXT}"]]\n',
        ),
        (
            "test_repeated_space_return.py",
            f"def command():\n    return {RAW_REPEATED_SPACE_PLAYBOOK_TEXT!r}\n",
        ),
        (
            "test_tabbed_default.py",
            f"def invoke(command={RAW_TABBED_PLAYBOOK_TEXT!r}):\n    pass\n",
        ),
        (
            "test_multiline_nested.py",
            f"WRAPPED = [[{RAW_MULTILINE_PLAYBOOK_TEXT!r}]]\n",
        ),
        ("fixture.yml", f"command: {RAW_LOCKED_PLAYBOOK_TEXT}\n"),
        (
            "list-fixture.yml",
            "command:\n  - uv\n  - run\n  - --locked\n  - ansible-playbook\n",
        ),
    ],
)
def test_raw_locked_playbook_literal_names_the_shared_helper(
    tmp_path: Path,
    relative_path: str,
    source: str,
) -> None:
    test_tree = tmp_path / "tests"
    test_tree.mkdir()
    offender = test_tree / relative_path
    offender.write_text(source, encoding="utf-8")

    with pytest.raises(
        AssertionError, match="ansible_playbook_command"
    ) as failure:
        assert_no_raw_playbook_literals([offender])
    assert str(offender) in str(failure.value)


def test_tracked_test_tree_has_no_raw_locked_playbook_literals() -> None:
    helper = load_helper()
    helper_path = Path(helper.__file__).resolve()
    result = subprocess.run(
        ["git", "ls-files", "tests"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    test_paths = [
        REPO_ROOT / relative_path
        for relative_path in result.stdout.splitlines()
        if (REPO_ROOT / relative_path).resolve() != helper_path
    ]

    assert_no_raw_playbook_literals(test_paths)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("ANSIBLE_INVENTORY", None),
        ("ANSIBLE_INVENTORY", "/different/inventory.yml"),
        ("ANSIBLE_VAULT_PASSWORD_FILE", None),
        ("ANSIBLE_VAULT_PASSWORD_FILE", "/different/vault-pass"),
    ],
)
def test_fixture_environment_failure_names_the_supported_test_command(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str | None,
) -> None:
    set_fixture_environment(monkeypatch)
    if value is None:
        monkeypatch.delenv(variable)
    else:
        monkeypatch.setenv(variable, value)
    helper = load_helper()

    with pytest.raises(AssertionError, match=r"\./validate\.sh tests"):
        helper.ansible_playbook_command("fixture.yml")


def test_explicit_own_inventory_mode_retains_the_vault_fixture_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_fixture_environment(monkeypatch)
    monkeypatch.delenv("ANSIBLE_INVENTORY")
    helper = load_helper()

    assert helper.ansible_playbook_command(
        "fixture.yml",
        "--inventory",
        "test-inventory.yml",
        supplies_own_inventory=True,
    ) == [
        *RAW_LOCKED_PLAYBOOK,
        "fixture.yml",
        "--inventory",
        "test-inventory.yml",
    ]


def test_own_inventory_mode_still_requires_the_fixture_vault_password_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_fixture_environment(monkeypatch)
    monkeypatch.delenv("ANSIBLE_INVENTORY")
    monkeypatch.delenv("ANSIBLE_VAULT_PASSWORD_FILE")
    helper = load_helper()

    with pytest.raises(AssertionError, match=r"\./validate\.sh tests"):
        helper.ansible_playbook_command(
            "fixture.yml",
            "--inventory",
            "test-inventory.yml",
            supplies_own_inventory=True,
        )


def test_effective_fixture_inventory_uses_only_non_resolving_connection_targets() -> None:
    assert (FIXTURE_ROOT / "vault-pass").read_text(encoding="utf-8") == (
        "unused-fixture-placeholder\n"
    )
    environment = os.environ.copy()
    environment.update(
        {
            "ANSIBLE_INVENTORY": str(FIXTURE_ROOT / "inventory.yml"),
            "ANSIBLE_VAULT_PASSWORD_FILE": str(FIXTURE_ROOT / "vault-pass"),
        }
    )
    result = subprocess.run(
        ["uv", "run", "--locked", "ansible-inventory", "--list"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=environment,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    hostvars = json.loads(result.stdout)["_meta"]["hostvars"]
    expected_targets = {
        "auth": "auth.invalid.",
        "portal": "portal.invalid.",
        "workstation": "workstation.invalid.",
    }
    assert set(hostvars) == set(expected_targets)

    forbidden_connection_settings = {
        "ansible_connection",
        "ansible_password",
        "ansible_port",
        "ansible_private_key_file",
        "ansible_ssh_common_args",
        "ansible_ssh_extra_args",
        "ansible_ssh_pass",
        "ansible_ssh_private_key_file",
        "ansible_user",
    }
    for alias, target in expected_targets.items():
        effective_vars = hostvars[alias]
        assert effective_vars["ansible_host"] == target
        assert forbidden_connection_settings.isdisjoint(effective_vars)

        with pytest.raises(socket.gaierror):
            socket.getaddrinfo(target, None)
