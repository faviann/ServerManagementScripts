#!/usr/bin/env python3
"""Regression coverage for the public vault command boundary."""

from __future__ import annotations

import subprocess
import os
import shutil
import errno
import fcntl
import pty
import select
import termios
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "vault.sh"
HEADER = "$ANSIBLE_VAULT;1.1;AES256\n"
VALID_YAML = """---
vault_proxmox_api_user: operator@pve
vault_proxmox_api_token_id: automation
vault_proxmox_api_token_secret: synthetic-token-marker
unrelated_scalar: keep-me
unrelated_mapping:
  nested: true
"""


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

    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
if args[:2] != ["run", "--locked"]:
    raise SystemExit(90)
command = args[2:]
for raw_path in command:
    path = Path(raw_path)
    if not path.exists() or not path.is_absolute():
        continue
    tracked = Path(os.environ["VAULT_TEST_REPO"]) / "inventory/group_vars/all/vault.yml"
    if path == tracked:
        continue
    if not str(path).startswith("/dev/shm/homelab-vault."):
        raise SystemExit(95)
    workspace = next(parent for parent in path.parents if parent.parent == Path("/dev/shm"))
    if workspace.stat().st_mode & 0o777 != 0o700:
        raise SystemExit(96)
if command[:1] == ["python"]:
    if os.environ.get("VAULT_TEST_PYTHON_FAIL") == "1":
        raise SystemExit(98)
    raise SystemExit(subprocess.run([sys.executable, *command[1:]]).returncode)
if command[:2] == ["ansible-vault", "view"]:
    if os.environ.get("VAULT_TEST_DECRYPT_FAIL") == "1" or (
        os.environ.get("VAULT_TEST_VALIDATE_FAIL") == "1"
        and Path(command[-1]).name == "vault.encrypted"
    ):
        raise SystemExit(91)
    data = Path(command[-1]).read_text(encoding="utf-8")
    if not data.startswith("$ANSIBLE_VAULT;"):
        raise SystemExit(92)
    sys.stdout.write(data.split("\\n", 1)[1])
    raise SystemExit(0)
if command[:2] == ["ansible-vault", "decrypt"]:
    if os.environ.get("VAULT_TEST_DECRYPT_FAIL") == "1":
        raise SystemExit(91)
    output = Path(command[command.index("--output") + 1])
    source = Path(command[-1])
    data = source.read_text(encoding="utf-8")
    if not data.startswith("$ANSIBLE_VAULT;"):
        raise SystemExit(92)
    output.write_text(data.split("\\n", 1)[1], encoding="utf-8")
    raise SystemExit(0)
if command[:2] == ["ansible-vault", "encrypt"]:
    if os.environ.get("VAULT_TEST_ENCRYPT_FAIL") == "1":
        raise SystemExit(93)
    target = Path(command[-1])
    target.write_text("$ANSIBLE_VAULT;1.1;AES256\\n" + target.read_text(encoding="utf-8"), encoding="utf-8")
    raise SystemExit(0)
raise SystemExit(94)
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "ANSIBLE_VAULT_PASSWORD_FILE": str(home / ".ansible/vault-pass"),
            "VAULT_TEST_REPO": str(repo),
        }
    )
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


def run_vault_tty(
    repo: Path,
    env: dict[str, str],
    interactions: list[tuple[str, str]],
    *args: str,
) -> tuple[int, str]:
    master, slave = pty.openpty()

    def make_controlling_terminal() -> None:
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

    process = subprocess.Popen(
        [str(repo / "vault.sh"), *args],
        cwd=repo,
        env=env,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        preexec_fn=make_controlling_terminal,
        close_fds=True,
    )
    os.close(slave)
    output = bytearray()
    interaction_index = 0
    while process.poll() is None:
        ready, _, _ = select.select([master], [], [], 0.1)
        if ready:
            try:
                output.extend(os.read(master, 4096))
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                break
        if interaction_index < len(interactions):
            prompt, response = interactions[interaction_index]
            if prompt.encode() in output:
                if "secret" in prompt.lower():
                    for _ in range(100):
                        if not termios.tcgetattr(master)[3] & termios.ECHO:
                            break
                        select.select([], [], [], 0.01)
                os.write(master, response.encode())
                interaction_index += 1
    process.wait(timeout=10)
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError as error:
            if error.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        output.extend(chunk)
    os.close(master)
    return process.returncode, output.decode(errors="replace")


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


@pytest.mark.parametrize(
    ("state", "expected_failure"),
    [
        ("valid", None),
        ("bad-header", "vault header"),
        ("invalid-header", "vault header"),
        ("missing-vault", "vault header"),
        ("missing-pass", "passphrase file"),
        ("empty-pass", "passphrase nonempty"),
        ("open-pass", "passphrase permissions"),
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
    elif state == "open-pass":
        pass_file.chmod(0o640)
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
    stat_stub = Path(env["PATH"].split(":", 1)[0]) / "stat"
    stat_stub.write_text(
        """#!/bin/bash
if [[ "$2" == %u ]]; then
    printf '%s\n' 2147483647
else
    exec /usr/bin/stat "$@"
fi
""",
        encoding="utf-8",
    )
    stat_stub.chmod(0o755)

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
            "''",
            "REPLACE_ME",
            "<REPLACE_ME>",
            "REPLACE_WITH_SYNTHETIC",
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
                content_lines.append(f"{key}: {replacement}")
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


def test_configure_replaces_only_credentials_through_a_tty_transaction(
    vault_repo: tuple[Path, dict[str, str]],
) -> None:
    repo, env = vault_repo
    vault = repo / "inventory/group_vars/all/vault.yml"
    original = HEADER + VALID_YAML
    vault.write_text(original, encoding="utf-8")
    Path(env["ANSIBLE_VAULT_PASSWORD_FILE"]).write_text("pass\n", encoding="utf-8")
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
    for marker in ("replacement-secret-marker", "keep-me", "synthetic-token-marker"):
        assert marker not in output
    assert list(repo.rglob("*backup*")) == []


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


def configure_interactions() -> list[tuple[str, str]]:
    return [
        ("Proxmox API user: ", "replacement@pve\n"),
        ("Proxmox API token ID: ", "replacement-id\n"),
        ("Proxmox API token secret: ", "replacement-secret-marker\n"),
    ]


def install_editor(tmp_path: Path, env: dict[str, str], *, fail: bool = False) -> Path:
    capture = tmp_path / "editor-capture"
    editor = Path(env["PATH"].split(":", 1)[0]) / "editor"
    editor.write_text(
        f"""#!/bin/bash
set -eu
test "$(dirname "$1")" = /dev/shm/homelab-vault.*
test "$(stat -c %a "$(dirname "$1")")" = 700
grep -q 'vault_proxmox_api_token_secret' "$1"
grep -q 'unrelated_mapping' "$1"
printf 'complete' > {capture}
{'exit 97' if fail else "printf '\\neditor_added: changed\\n' >> \"$1\""}
""",
        encoding="utf-8",
    )
    editor.chmod(0o755)
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
    capture = install_editor(tmp_path, env)

    returncode, output = run_vault_tty(repo, env, [], "edit")

    assert returncode == 0, output
    assert capture.read_text(encoding="utf-8") == "complete"
    published = vault.read_text(encoding="utf-8")
    assert published.startswith(HEADER)
    assert "editor_added: changed" in published
    assert "unrelated_scalar: keep-me" in published
    assert "synthetic-token-marker" not in output
    assert list(repo.rglob("*backup*")) == []


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
        ("configure", "yaml"),
        ("configure", "encrypt"),
        ("configure", "validation"),
        ("edit", "decrypt"),
        ("edit", "editor"),
        ("edit", "encrypt"),
        ("edit", "validation"),
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
    body = "invalid: [yaml\n" if failure == "yaml" else VALID_YAML
    original = (HEADER + body).encode()
    vault.write_bytes(original)
    if failure == "decrypt":
        env["VAULT_TEST_DECRYPT_FAIL"] = "1"
    elif failure == "encrypt":
        env["VAULT_TEST_ENCRYPT_FAIL"] = "1"
    elif failure == "validation":
        env["VAULT_TEST_VALIDATE_FAIL"] = "1"
    if operation == "edit":
        install_editor(tmp_path, env, fail=failure == "editor")

    interactions = configure_interactions() if operation == "configure" else []
    returncode, output = run_vault_tty(repo, env, interactions, operation)

    assert returncode == 1, output
    assert vault.read_bytes() == original
    assert not list(repo.rglob("*.tmp.*"))
    assert list(repo.rglob("*backup*")) == []
