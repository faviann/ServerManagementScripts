#!/usr/bin/env python3
"""Regression coverage for the public vault command boundary."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import vault_test_harness
from vault_test_harness import run_vault_tty


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "vault.sh"
FAKE_FIXTURES = Path(__file__).parent / "fixtures/vault"
HEADER = "$ANSIBLE_VAULT;1.1;AES256\n"
VALID_YAML = """---
vault_proxmox_api_user: operator@pve
vault_proxmox_api_token_id: automation
vault_proxmox_api_token_secret: synthetic-token-marker
unrelated_scalar: keep-me
unrelated_mapping:
  nested: true
"""


def install_fake_executable(bin_dir: Path, name: str) -> Path:
    executable = bin_dir / name
    shutil.copy2(FAKE_FIXTURES / name, executable)
    executable.chmod(0o755)
    return executable


@pytest.fixture
def vault_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    vault_dir = repo / "inventory/group_vars/all"
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    vault_dir.mkdir(parents=True)
    bin_dir.mkdir()
    (home / ".ansible").mkdir(parents=True)
    shutil.copy2(RUNNER, repo / "vault.sh")

    for name in ("uv", "mv", "rm"):
        install_fake_executable(bin_dir, name)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "ANSIBLE_VAULT_PASSWORD_FILE": str(home / ".ansible/vault-pass"),
            "VAULT_TEST_REPO": str(repo),
            "VAULT_TEST_BOUNDARY_CAPTURE": str(tmp_path / "boundaries.jsonl"),
        }
    )
    return repo, env


@pytest.fixture
def real_vault_repo(
    vault_repo: tuple[Path, dict[str, str]],
) -> tuple[Path, dict[str, str]]:
    repo, env = vault_repo
    shutil.copy2(REPO_ROOT / "pyproject.toml", repo / "pyproject.toml")
    shutil.copy2(REPO_ROOT / "uv.lock", repo / "uv.lock")
    (repo / ".venv").symlink_to(REPO_ROOT / ".venv", target_is_directory=True)
    env["PATH"] = env["PATH"].split(":", 1)[1]
    passphrase_file = Path(env["ANSIBLE_VAULT_PASSWORD_FILE"])
    passphrase_file.write_text("real-smoke-passphrase\n", encoding="utf-8")
    passphrase_file.chmod(0o600)
    return repo, env


def run_vault(
    repo: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(repo / "vault.sh"), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def run_real_ansible_vault(
    repo: Path, env: dict[str, str], operation: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/ansible-vault"),
            operation,
            str(repo / "inventory/group_vars/all/vault.yml"),
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def boundary_events(env: dict[str, str]) -> list[dict[str, object]]:
    capture = Path(env["VAULT_TEST_BOUNDARY_CAPTURE"])
    return [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]


def assert_no_transaction_artifacts(repo: Path, env: dict[str, str]) -> None:
    workspaces = {
        Path(str(event["workspace"]))
        for event in boundary_events(env)
        if "workspace" in event
    }
    assert workspaces
    assert not [workspace for workspace in workspaces if workspace.exists()]
    for root in (repo, Path(env["HOME"])):
        assert not [
            path
            for path in root.rglob("*")
            if "backup" in path.name.lower()
            or ".tmp." in path.name
            or path.name.startswith("homelab-vault.")
        ]


def test_vault_requires_an_operation_and_advertises_its_complete_interface() -> None:
    missing = subprocess.run(
        [str(RUNNER)], capture_output=True, text=True, timeout=10
    )
    help_result = subprocess.run(
        [str(RUNNER), "--help"], capture_output=True, text=True, timeout=10
    )

    assert missing.returncode == 2
    assert help_result.returncode == 0
    assert {"configure", "edit", "check"} <= set(help_result.stdout.split())


def test_vault_fakes_are_named_executable_fixtures(
    vault_repo: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    _, env = vault_repo
    installed_bin = Path(env["PATH"].split(":", 1)[0])
    install_editor(tmp_path, env)

    for name in ("uv", "mv", "rm", "editor"):
        fixture = FAKE_FIXTURES / name
        assert fixture.is_file()
        assert installed_bin.joinpath(name).read_bytes() == fixture.read_bytes()


def test_vault_uses_its_project_when_invoked_from_an_unrelated_directory(
    vault_repo: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, env = vault_repo
    (repo / "inventory/group_vars/all/vault.yml").write_text(
        HEADER + VALID_YAML, encoding="utf-8"
    )
    pass_file = Path(env["ANSIBLE_VAULT_PASSWORD_FILE"])
    pass_file.write_text("synthetic-passphrase-marker\n", encoding="utf-8")
    pass_file.chmod(0o600)
    unrelated_directory = tmp_path / "unrelated"
    unrelated_directory.mkdir()
    env["VAULT_TEST_REQUIRE_PROJECT_CWD"] = "1"

    result = subprocess.run(
        [str(repo / "vault.sh"), "check"],
        cwd=unrelated_directory,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "decryptability: PASS" in result.stdout
    assert "YAML mapping: PASS" in result.stdout


def test_real_ansible_vault_encrypt_decrypt_round_trip(
    real_vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = real_vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    vault.write_text(VALID_YAML, encoding="utf-8")

    encrypted = run_real_ansible_vault(repo, env, "encrypt")
    ciphertext = vault.read_text(encoding="utf-8")
    decrypted = run_real_ansible_vault(repo, env, "decrypt")

    assert encrypted.returncode == 0, encrypted.stderr
    assert ciphertext.startswith(HEADER)
    assert decrypted.returncode == 0, decrypted.stderr
    assert vault.read_text(encoding="utf-8") == VALID_YAML


def test_check_accepts_a_genuinely_encrypted_vault(
    real_vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = real_vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    vault.write_text(VALID_YAML, encoding="utf-8")
    encrypted = run_real_ansible_vault(repo, env, "encrypt")
    assert encrypted.returncode == 0, encrypted.stderr

    result = run_vault(repo, env, "check")

    assert result.returncode == 0, result.stderr
    assert all(line.endswith(": PASS") for line in result.stdout.splitlines())


@pytest.mark.parametrize(
    ("state", "expected_failure"),
    [
        ("valid", None),
        ("bad-header", "vault header"),
        ("invalid-header", "vault header"),
        ("missing-vault", "vault header"),
        ("missing-pass", "passphrase file"),
        ("empty-pass", "passphrase nonempty"),
        ("group-open-pass", "passphrase permissions"),
        ("world-open-pass", "passphrase permissions"),
        ("decrypt-failure", "decryptability"),
        ("non-mapping", "YAML mapping"),
        ("missing-user", "vault_proxmox_api_user"),
        ("empty-token-id", "vault_proxmox_api_token_id"),
        ("placeholder-secret", "vault_proxmox_api_token_secret"),
    ],
)
def test_check_reports_each_contract_check_without_disclosing_values(
    vault_repo: tuple[Path, dict[str, str]], state: str, expected_failure: str | None
) -> None:
    repo, env = vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    pass_file = Path(env["ANSIBLE_VAULT_PASSWORD_FILE"])
    pass_file.write_text("synthetic-passphrase-marker\n", encoding="utf-8")
    pass_file.chmod(0o600)
    content = VALID_YAML
    if state == "non-mapping":
        content = "- not\n- a\n- mapping\n"
    elif state == "missing-user":
        content = content.replace("vault_proxmox_api_user: operator@pve\n", "")
    elif state == "empty-token-id":
        content = content.replace("automation", "''")
    elif state == "placeholder-secret":
        content = content.replace("synthetic-token-marker", "REPLACE_ME")
    vault.write_text(HEADER + content, encoding="utf-8")
    if state == "bad-header":
        vault.write_text(content, encoding="utf-8")
    elif state == "invalid-header":
        vault.write_text("$ANSIBLE_VAULT;garbage\n" + content, encoding="utf-8")
    elif state == "missing-vault":
        vault.unlink()
    elif state == "missing-pass":
        pass_file.unlink()
    elif state == "empty-pass":
        pass_file.write_text("", encoding="utf-8")
    elif state == "group-open-pass":
        pass_file.chmod(0o640)
    elif state == "world-open-pass":
        pass_file.chmod(0o604)
    elif state == "decrypt-failure":
        env["VAULT_TEST_DECRYPT_FAIL"] = "1"

    result = run_vault(repo, env, "check")

    assert result.returncode == (0 if expected_failure is None else 1)
    output = result.stdout + result.stderr
    for marker in (
        "synthetic-passphrase-marker",
        "synthetic-token-marker",
        "operator@pve",
        "keep-me",
    ):
        assert marker not in output
    assert "vault header" in output
    assert "passphrase file" in output
    assert "passphrase nonempty" in output
    assert "passphrase ownership" in output
    assert "passphrase permissions" in output
    assert "decryptability" in output
    assert "YAML mapping" in output
    for key in (
        "vault_proxmox_api_user",
        "vault_proxmox_api_token_id",
        "vault_proxmox_api_token_secret",
    ):
        assert key in output
    assert all(
        line.endswith(": PASS") or line.endswith(": FAIL")
        for line in output.splitlines()
    )
    if expected_failure is not None:
        assert f"{expected_failure}: FAIL" in output


def test_check_rejects_a_passphrase_file_not_owned_by_the_current_user(
    vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = vault_repo
    (repo / "inventory/group_vars/all/vault.yml").write_text(
        HEADER + VALID_YAML, encoding="utf-8"
    )
    pass_file = Path(env["ANSIBLE_VAULT_PASSWORD_FILE"])
    pass_file.write_text("pass\n", encoding="utf-8")
    pass_file.chmod(0o600)
    stat_fixture = FAKE_FIXTURES / "stat"
    assert stat_fixture.is_file()
    stat_stub = install_fake_executable(
        Path(env["PATH"].split(":", 1)[0]), "stat"
    )
    assert stat_stub.read_bytes() == stat_fixture.read_bytes()

    result = run_vault(repo, env, "check")

    assert result.returncode == 1
    assert "passphrase ownership: FAIL" in result.stdout


def test_check_treats_yaml_validation_tool_failure_as_a_failed_check(
    vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = vault_repo
    (repo / "inventory/group_vars/all/vault.yml").write_text(
        HEADER + VALID_YAML, encoding="utf-8"
    )
    pass_file = Path(env["ANSIBLE_VAULT_PASSWORD_FILE"])
    pass_file.write_text("pass\n", encoding="utf-8")
    pass_file.chmod(0o600)
    env["VAULT_TEST_PYTHON_FAIL"] = "1"

    result = run_vault(repo, env, "check")

    assert result.returncode == 1
    assert "YAML mapping: FAIL" in result.stdout


@pytest.mark.parametrize(
    ("key", "replacement"),
    [
        (key, replacement)
        for key in (
            "vault_proxmox_api_user",
            "vault_proxmox_api_token_id",
            "vault_proxmox_api_token_secret",
        )
        for replacement in (
            None,
            "",
            "REPLACE_ME",
            "<REPLACE_ME>",
            "REPLACE_WITH_SYNTHETIC",
            "  REPLACE_ME\t",
            "\t<REPLACE_ME>  ",
            "  REPLACE_WITH_SYNTHETIC  ",
        )
    ],
)
def test_check_rejects_every_missing_empty_or_placeholder_required_key(
    vault_repo: tuple[Path, dict[str, str]], key: str, replacement: str | None
) -> None:
    repo, env = vault_repo
    lines = VALID_YAML.splitlines()
    content_lines = []
    for line in lines:
        if line.startswith(f"{key}:"):
            if replacement is not None:
                content_lines.append(f"{key}: {json.dumps(replacement)}")
        else:
            content_lines.append(line)
    (repo / "inventory/group_vars/all/vault.yml").write_text(
        HEADER + "\n".join(content_lines) + "\n", encoding="utf-8"
    )
    pass_file = Path(env["ANSIBLE_VAULT_PASSWORD_FILE"])
    pass_file.write_text("pass\n", encoding="utf-8")
    pass_file.chmod(0o600)

    result = run_vault(repo, env, "check")

    assert result.returncode == 1
    assert f"{key}: FAIL" in result.stdout


def test_check_accepts_ordinary_padded_values_without_changing_them(
    vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = vault_repo
    values = {
        "vault_proxmox_api_user": "  ordinary@pve  ",
        "vault_proxmox_api_token_id": "\tordinary-id  ",
        "vault_proxmox_api_token_secret": "  ordinary-secret\t",
    }
    body = "---\n" + "".join(
        f"{key}: {json.dumps(value)}\n" for key, value in values.items()
    )
    vault = repo / "inventory/group_vars/all/vault.yml"
    original = (HEADER + body).encode()
    vault.write_bytes(original)
    pass_file = Path(env["ANSIBLE_VAULT_PASSWORD_FILE"])
    pass_file.write_text("pass\n", encoding="utf-8")
    pass_file.chmod(0o600)

    result = run_vault(repo, env, "check")

    assert result.returncode == 0
    assert vault.read_bytes() == original


@pytest.mark.parametrize("signal_number", [signal.SIGINT, signal.SIGTERM])
def test_interrupted_check_removes_its_exact_plaintext_workspace(
    vault_repo: tuple[Path, dict[str, str]], signal_number: int
) -> None:
    repo, env = vault_repo
    (repo / "inventory/group_vars/all/vault.yml").write_text(
        HEADER + VALID_YAML, encoding="utf-8"
    )
    pass_file = Path(env["ANSIBLE_VAULT_PASSWORD_FILE"])
    pass_file.write_text("synthetic-passphrase-marker\n", encoding="utf-8")
    pass_file.chmod(0o600)
    env["VAULT_TEST_VIEW_SIGNAL"] = str(signal_number)
    result = run_vault(repo, env, "check")

    plaintext_event = next(
        event
        for event in boundary_events(env)
        if event["boundary"] == "view-plaintext"
    )
    workspace = Path(str(plaintext_event["workspace"]))
    workspace_was_removed = not workspace.exists()
    if not workspace_was_removed:
        shutil.rmtree(workspace)

    assert result.returncode != 0
    assert workspace_was_removed
    cleanup_targets = [
        Path(str(event["workspace"]))
        for event in boundary_events(env)
        if event["boundary"] == "cleanup"
    ]
    assert cleanup_targets == [workspace]
    assert_no_transaction_artifacts(repo, env)
    output = result.stdout + result.stderr
    for marker in (
        "synthetic-passphrase-marker",
        "synthetic-token-marker",
        "operator@pve",
        "keep-me",
    ):
        assert marker not in output


def test_configure_replaces_only_credentials_through_a_tty_transaction(
    vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    original = HEADER + VALID_YAML
    vault.write_text(original, encoding="utf-8")
    Path(env["ANSIBLE_VAULT_PASSWORD_FILE"]).write_text(
        "synthetic-passphrase-marker\n", encoding="utf-8"
    )
    env.update(
        {
            "PROXMOX_API_USER": "environment-user-must-not-win",
            "PROXMOX_API_TOKEN_ID": "environment-id-must-not-win",
            "PROXMOX_API_TOKEN_SECRET": "environment-secret-must-not-win",
        }
    )

    returncode, output = run_vault_tty(
        repo,
        env,
        [
            ("Proxmox API user: ", "replacement@pve\n"),
            ("Proxmox API token ID: ", "replacement-id\n"),
            ("Proxmox API token secret: ", "replacement-secret-marker\n"),
        ],
        "configure",
    )

    assert returncode == 0, output
    published = vault.read_text(encoding="utf-8")
    assert published.startswith(HEADER)
    plaintext = published.removeprefix(HEADER)
    assert "replacement@pve" in plaintext
    assert "replacement-id" in plaintext
    assert "replacement-secret-marker" in plaintext
    assert "unrelated_scalar: keep-me" in plaintext
    assert "nested: true" in plaintext
    assert "environment-" not in plaintext
    for marker in (
        "synthetic-passphrase-marker",
        "replacement-secret-marker",
        "keep-me",
        "synthetic-token-marker",
    ):
        assert marker not in output
    assert list(repo.rglob("*backup*")) == []


def test_tty_runner_does_not_capture_secret_responses(
    vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = vault_repo
    secret = "transcript-secret-marker"

    returncode, transcript = run_vault_tty(
        repo,
        env,
        configure_interactions(token_secret=secret),
        "configure",
    )

    assert returncode == 0, transcript
    assert secret not in transcript


def test_tty_runner_does_not_send_a_secret_when_echo_stays_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = "unsent-secret-marker"
    sent: list[str] = []

    class EchoEnabledChild:
        before = ""
        exitstatus = 1
        signalstatus = None

        def expect_exact(self, patterns: object) -> int:
            return 0

        def waitnoecho(self) -> bool:
            return False

        def send(self, response: str) -> None:
            sent.append(response)

        def eof(self) -> bool:
            return False

        def expect(self, pattern: object) -> int:
            return 0

        def close(self, force: bool = False) -> None:
            pass

    monkeypatch.setattr(
        vault_test_harness.pexpect,
        "spawn",
        lambda *args, **kwargs: EchoEnabledChild(),
    )

    returncode, transcript = vault_test_harness.run_vault_tty(
        tmp_path,
        {},
        [("Token secret: ", secret + "\n")],
        "configure",
    )

    assert returncode != 0
    assert sent == []
    assert secret not in transcript


def test_configure_rejects_non_tty_and_all_extra_arguments(
    vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = vault_repo

    assert run_vault(repo, env, "configure").returncode == 1
    assert run_vault(repo, env, "configure", "value").returncode == 2
    assert run_vault(repo, env, "edit", "value").returncode == 2
    assert run_vault(repo, env, "check", "value").returncode == 2
    assert run_vault(repo, env, "unknown").returncode == 2


def test_legacy_configure_command_is_absent_without_a_shim() -> None:
    assert not os.path.lexists(REPO_ROOT / "configure-vault.sh")


def configure_interactions(
    api_user: str = "replacement@pve",
    token_id: str = "replacement-id",
    token_secret: str = "replacement-secret-marker",
) -> list[tuple[str, str]]:
    return [
        ("Proxmox API user: ", f"{api_user}\n"),
        ("Proxmox API token ID: ", f"{token_id}\n"),
        ("Proxmox API token secret: ", f"{token_secret}\n"),
    ]


@pytest.mark.parametrize("key_index", range(3))
@pytest.mark.parametrize(
    "invalid_value",
    [
        "   ",
        "REPLACE_ME",
        "<REPLACE_ME>",
        "REPLACE_WITH_SYNTHETIC",
        "  REPLACE_ME\t",
        "\t<REPLACE_ME>  ",
        "  REPLACE_WITH_SYNTHETIC  ",
    ],
)
def test_configure_rejects_every_value_that_check_rejects(
    vault_repo: tuple[Path, dict[str, str]], key_index: int, invalid_value: str
) -> None:
    repo, env = vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    original = (HEADER + VALID_YAML).encode()
    vault.write_bytes(original)
    values = ["replacement@pve", "replacement-id", "replacement-secret-marker"]
    values[key_index] = invalid_value

    returncode, output = run_vault_tty(
        repo, env, configure_interactions(*values), "configure"
    )

    assert returncode == 1
    assert vault.read_bytes() == original
    assert "replacement-secret-marker" not in output
    assert "synthetic-token-marker" not in output
    assert "keep-me" not in output


def test_configure_preserves_ordinary_credential_whitespace_exactly(
    vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = vault_repo
    values = ("  ordinary@pve  ", "\tordinary-id  ", "  ordinary-secret\t")

    returncode, output = run_vault_tty(
        repo, env, configure_interactions(*values), "configure"
    )

    assert returncode == 0, output
    plaintext = (
        repo / "inventory/group_vars/all/vault.yml"
    ).read_text(encoding="utf-8").removeprefix(HEADER)
    parsed = yaml.safe_load(plaintext)
    assert tuple(
        parsed[key]
        for key in (
            "vault_proxmox_api_user",
            "vault_proxmox_api_token_id",
            "vault_proxmox_api_token_secret",
        )
    ) == values


def install_editor(
    tmp_path: Path,
    env: dict[str, str],
    *,
    fail: bool = False,
    signal_number: int | None = None,
) -> Path:
    capture = tmp_path / "editor-capture"
    editor = install_fake_executable(
        Path(env["PATH"].split(":", 1)[0]), "editor"
    )
    env["VAULT_TEST_EDITOR_CAPTURE"] = str(capture)
    if fail:
        env["VAULT_TEST_EDITOR_FAIL"] = "1"
    if signal_number is not None:
        env["VAULT_TEST_EDITOR_SIGNAL"] = str(int(signal_number))
    env["EDITOR"] = str(editor)
    return capture


def test_configure_creates_only_required_fields_in_a_protected_tmpfs_transaction(
    vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = vault_repo

    returncode, output = run_vault_tty(
        repo, env, configure_interactions(), "configure"
    )

    assert returncode == 0, output
    vault = repo / "inventory/group_vars/all/vault.yml"
    plaintext = vault.read_text(encoding="utf-8").removeprefix(HEADER)
    assert set(
        line.split(":", 1)[0] for line in plaintext.splitlines() if ":" in line
    ) == {
        "vault_proxmox_api_user",
        "vault_proxmox_api_token_id",
        "vault_proxmox_api_token_secret",
    }
    assert vault.stat().st_mode & 0o777 == 0o600
    assert not list(repo.rglob("*.tmp.*"))


def test_edit_presents_and_publishes_the_complete_vault(
    vault_repo: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, env = vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    vault.write_text(HEADER + VALID_YAML, encoding="utf-8")
    Path(env["ANSIBLE_VAULT_PASSWORD_FILE"]).write_text(
        "synthetic-passphrase-marker\n", encoding="utf-8"
    )
    capture = install_editor(tmp_path, env)

    returncode, output = run_vault_tty(repo, env, [], "edit")

    assert returncode == 0, output
    assert capture.read_text(encoding="utf-8") == "complete"
    published = vault.read_text(encoding="utf-8")
    assert published.startswith(HEADER)
    assert "editor_added: editor-secret-marker" in published
    assert "unrelated_scalar: keep-me" in published
    for marker in (
        "synthetic-passphrase-marker",
        "synthetic-token-marker",
        "keep-me",
        "editor-secret-marker",
    ):
        assert marker not in output
    assert list(repo.rglob("*backup*")) == []


@pytest.mark.parametrize(
    ("operation", "initial_state"),
    [
        ("configure", "absent"),
        ("configure", "ciphertext"),
        ("configure", "plaintext"),
        ("edit", "ciphertext"),
        ("edit", "plaintext"),
    ],
)
def test_mutations_keep_the_tracked_path_safe_at_every_external_boundary(
    vault_repo: tuple[Path, dict[str, str]],
    tmp_path: Path,
    operation: str,
    initial_state: str,
) -> None:
    repo, env = vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    original = VALID_YAML.encode()
    if initial_state == "ciphertext":
        original = (HEADER + VALID_YAML).encode()
        vault.write_bytes(original)
    elif initial_state == "plaintext":
        vault.write_bytes(original)
    if operation == "edit":
        install_editor(tmp_path, env)
    interactions: list[tuple[str, str]] = []
    if initial_state == "plaintext":
        interactions.append(("Encrypt and replace it? [y/N] ", "y\n"))
    if operation == "configure":
        interactions.extend(configure_interactions())
    returncode, output = run_vault_tty(repo, env, interactions, operation)

    assert returncode == 0, output
    events = boundary_events(env)
    expected_state = initial_state
    assert events
    assert {event["tracked_state"] for event in events} == {expected_state}
    if initial_state == "plaintext":
        original_digest = hashlib.sha256(original).hexdigest()
        assert {event["tracked_digest"] for event in events} == {original_digest}
    move_events = [event for event in events if event["boundary"] == "move"]
    assert len(move_events) == 1
    move = move_events[0]
    assert move["same_directory"] is True
    assert move["source_ciphertext"] is True
    assert Path(str(move["destination"])) == vault
    assert Path(str(move["source"])).parent == vault.parent
    editor_events = [event for event in events if event["boundary"] == "editor"]
    assert len(editor_events) == (1 if operation == "edit" else 0)
    cleanup_events = [event for event in events if event["boundary"] == "cleanup"]
    assert len(cleanup_events) == 1
    assert cleanup_events[0]["tracked_state"] == expected_state
    assert vault.read_text(encoding="utf-8").startswith(HEADER)
    assert_no_transaction_artifacts(repo, env)


def test_cleanup_ignores_an_unrelated_tmpfs_transaction(
    vault_repo: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, env = vault_repo
    sentinel = Path("/dev/shm") / f"homelab-vault.unrelated-{tmp_path.name}"
    sentinel.mkdir()
    try:
        returncode, transcript = run_vault_tty(
            repo, env, configure_interactions(), "configure"
        )

        assert returncode == 0, transcript
        assert_no_transaction_artifacts(repo, env)
        assert sentinel.is_dir()
    finally:
        sentinel.rmdir()


@pytest.mark.parametrize("operation", ["configure", "edit"])
def test_unencrypted_vault_requires_confirmation_and_retains_no_plaintext_backup(
    vault_repo: tuple[Path, dict[str, str]], tmp_path: Path, operation: str
) -> None:
    repo, env = vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    original = VALID_YAML.encode()
    vault.write_bytes(original)
    if operation == "edit":
        install_editor(tmp_path, env)

    declined, declined_output = run_vault_tty(
        repo,
        env,
        [("Encrypt and replace it? [y/N] ", "n\n")],
        operation,
    )
    assert declined == 1, declined_output
    assert vault.read_bytes() == original

    interactions = [("Encrypt and replace it? [y/N] ", "y\n")]
    if operation == "configure":
        interactions.extend(configure_interactions())
    accepted, accepted_output = run_vault_tty(repo, env, interactions, operation)

    assert accepted == 0, accepted_output
    assert vault.read_text(encoding="utf-8").startswith(HEADER)
    assert list(repo.rglob("*backup*")) == []
    assert not any(
        path.is_file() and path.read_bytes() == original
        for path in repo.rglob("*")
        if path != vault
    )


@pytest.mark.parametrize(
    ("operation", "failure"),
    [
        ("configure", "decrypt"),
        ("configure", "decrypt-output"),
        ("configure", "yaml"),
        ("configure", "encrypt"),
        ("configure", "validation"),
        ("configure", "publication"),
        ("edit", "decrypt"),
        ("edit", "decrypt-output"),
        ("edit", "editor"),
        ("edit", "encrypt"),
        ("edit", "validation"),
        ("edit", "publication"),
    ],
)
def test_failed_mutation_preserves_the_original_bytes(
    vault_repo: tuple[Path, dict[str, str]],
    tmp_path: Path,
    operation: str,
    failure: str,
) -> None:
    repo, env = vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    body = VALID_YAML + "invalid: [yaml\n" if failure == "yaml" else VALID_YAML
    original = (HEADER + body).encode()
    vault.write_bytes(original)
    passphrase = "synthetic-passphrase-marker"
    Path(env["ANSIBLE_VAULT_PASSWORD_FILE"]).write_text(
        passphrase + "\n", encoding="utf-8"
    )
    if failure == "decrypt":
        env["VAULT_TEST_DECRYPT_FAIL"] = "1"
    elif failure == "decrypt-output":
        env["VAULT_TEST_DECRYPT_WRITES_THEN_FAIL"] = "1"
    elif failure == "encrypt":
        env["VAULT_TEST_ENCRYPT_FAIL"] = "1"
    elif failure == "validation":
        env["VAULT_TEST_VALIDATE_FAIL"] = "1"
    elif failure == "publication":
        env["VAULT_TEST_MOVE_FAIL"] = "1"
    if operation == "edit":
        install_editor(tmp_path, env, fail=failure == "editor")

    interactions = configure_interactions() if operation == "configure" else []
    returncode, output = run_vault_tty(repo, env, interactions, operation)

    assert returncode == 1, output
    assert vault.read_bytes() == original
    markers = ["synthetic-token-marker", "keep-me", passphrase]
    if operation == "configure":
        markers.append("replacement-secret-marker")
    elif failure not in ("decrypt", "decrypt-output"):
        markers.append("editor-secret-marker")
    for marker in markers:
        assert marker not in output
    events = boundary_events(env)
    original_digest = hashlib.sha256(original).hexdigest()
    assert events
    assert {event["tracked_state"] for event in events} == {"ciphertext"}
    assert {event["tracked_digest"] for event in events} == {original_digest}
    if failure == "publication":
        move_events = [event for event in events if event["boundary"] == "move"]
        assert len(move_events) == 1
        move = move_events[0]
        assert move["same_directory"] is True
        assert move["source_ciphertext"] is True
        assert Path(str(move["source"])).parent == vault.parent
        assert Path(str(move["destination"])) == vault
    elif failure in ("decrypt", "decrypt-output"):
        assert not [event for event in events if event["boundary"] == "move"]
    assert len([event for event in events if event["boundary"] == "cleanup"]) == 1
    assert_no_transaction_artifacts(repo, env)


@pytest.mark.parametrize("signal_number", [signal.SIGINT, signal.SIGTERM])
def test_interrupted_edit_cleans_its_exact_transaction_workspace(
    vault_repo: tuple[Path, dict[str, str]],
    tmp_path: Path,
    signal_number: int,
) -> None:
    repo, env = vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    original = (HEADER + VALID_YAML).encode()
    vault.write_bytes(original)
    install_editor(tmp_path, env, signal_number=signal_number)
    returncode, _ = run_vault_tty(repo, env, [], "edit")
    events = boundary_events(env)
    editor_event = next(event for event in events if event["boundary"] == "editor")
    workspace = Path(str(editor_event["workspace"]))
    workspace_was_removed = not workspace.exists()
    if not workspace_was_removed:
        shutil.rmtree(workspace)

    assert returncode == 1
    assert workspace_was_removed
    assert vault.read_bytes() == original
    assert len([event for event in events if event["boundary"] == "cleanup"]) == 1
    assert_no_transaction_artifacts(repo, env)


def test_cleanup_status_failure_makes_a_successful_mutation_fail(
    vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    original = (HEADER + VALID_YAML).encode()
    vault.write_bytes(original)
    env["VAULT_TEST_CLEANUP_FAIL"] = "1"
    returncode, output = run_vault_tty(
        repo, env, configure_interactions(), "configure"
    )

    cleanup_event = next(
        event for event in boundary_events(env) if event["boundary"] == "cleanup"
    )
    assert returncode == 1
    assert "configure: PASS" not in output
    assert vault.read_bytes() == original
    assert not Path(str(cleanup_event["workspace"])).exists()
    assert len(
        [
            event
            for event in boundary_events(env)
            if event["boundary"] == "cleanup"
        ]
    ) == 1
    assert_no_transaction_artifacts(repo, env)
    assert not list(repo.rglob("vault.yml.tmp.*"))


def test_signal_between_cleanup_and_publication_preserves_the_original(
    vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    original = (HEADER + VALID_YAML).encode()
    vault.write_bytes(original)
    env["VAULT_TEST_CLEANUP_SIGNAL"] = "1"
    returncode, output = run_vault_tty(
        repo, env, configure_interactions(), "configure"
    )

    assert returncode == 1
    assert "configure: PASS" not in output
    assert vault.read_bytes() == original
    assert_no_transaction_artifacts(repo, env)
    assert not list(repo.rglob("vault.yml.tmp.*"))


def test_signal_after_atomic_rename_reports_the_committed_success(
    vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    original = (HEADER + VALID_YAML).encode()
    vault.write_bytes(original)
    env["VAULT_TEST_MOVE_SIGNAL_AFTER"] = "1"
    returncode, output = run_vault_tty(
        repo, env, configure_interactions(), "configure"
    )

    assert returncode == 0
    assert "configure: PASS" in output
    assert vault.read_bytes() != original
    assert vault.read_text(encoding="utf-8").startswith(HEADER)
    assert_no_transaction_artifacts(repo, env)
    assert not list(repo.rglob("vault.yml.tmp.*"))
