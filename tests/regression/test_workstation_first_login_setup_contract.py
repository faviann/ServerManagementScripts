#!/usr/bin/env python3
"""Regression test for workstation first-login setup contract."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _write_activation_package(path: Path) -> None:
    _write_executable(
        path / "activate",
        """#!/bin/sh
printf '%s %s\n' "$0" "$*" >> "$COMMAND_LOG"
test "$(cat "$ACTIVE_GENERATION")" = "${0%/activate}" || {
  printf 'activation ran before its generation became active\n' >&2
  exit 1
}
test "$#" -eq 2 && test "$1" = "--driver-version" && test "$2" = "1" || {
  printf 'activation requires --driver-version 1\n' >&2
  exit 1
}
if [ "${ACTIVATION_BREAK_TOOL:-0}" = "1" ]; then
  rm -f "$HOME/.local/bin/workstation-update"
fi
""",
    )


def _prepare_completed_workstation(temp_root: Path) -> tuple[Path, dict[str, str]]:
    username = subprocess.run(
        ["id", "-un"], check=True, text=True, stdout=subprocess.PIPE
    ).stdout.strip()
    home = temp_root / "home" / username
    bin_dir = home / ".local" / "bin"
    source = home / ".local" / "share" / "chezmoi"
    command_log = temp_root / "commands.log"
    bw_state = temp_root / "bw-state"
    git_commit = temp_root / "git-commit"
    git_dirty = temp_root / "git-dirty"
    active_generation = temp_root / "active-generation"
    active_generation_read_count = temp_root / "active-generation-read-count"
    built_generation = temp_root / "built-generation"
    generation_a = temp_root / "nix" / "store" / "generation-a"

    for path in (
        home / ".ansible",
        home / ".ssh",
        bin_dir,
        source / ".git",
        source / "dot_local" / "bin",
    ):
        path.mkdir(parents=True, exist_ok=True)
    (home / ".ansible" / "vault-pass").write_text("test\n", encoding="utf-8")
    (home / ".ansible" / "vault-pass").chmod(0o600)
    (home / ".ssh" / "id_ed25519").write_text("test\n", encoding="utf-8")
    (home / ".ssh" / "id_ed25519").chmod(0o600)
    for name in ("id_ed25519.pub", "allowed_signers", "known_hosts"):
        (home / ".ssh" / name).write_text("test\n", encoding="utf-8")

    mock = """#!/bin/sh
name=$(basename "$0")
printf '%s %s\n' "$name" "$*" >> "$COMMAND_LOG"
case "$name:$1" in
  bw:status) printf '{"status":"%s"}\n' "$(cat "$BW_STATE")" ;;
  bw:unlock) printf 'unlocked' > "$BW_STATE"; printf 'test-session\n' ;;
  bw:login) printf 'locked' > "$BW_STATE" ;;
  ssh:*) printf "You've successfully authenticated\n" ;;
  git:-C)
    case "$3" in
      rev-parse)
        if [ "${GIT_COMMIT_READ_FAIL:-0}" = "1" ]; then
          exit 1
        fi
        if [ "${GIT_COMMIT_READ_EMPTY:-0}" != "1" ]; then
          cat "$GIT_COMMIT"
        fi
        ;;
      status)
        if [ "${GIT_STATUS_FAIL:-0}" = "1" ]; then
          exit 1
        fi
        cat "$GIT_DIRTY"
        ;;
    esac
    ;;
  readlink:-f)
    if [ "${ACTIVE_GENERATION_READ_FAIL:-0}" = "1" ]; then
      exit 1
    fi
    if [ -n "${ACTIVE_GENERATION_READ_EMPTY_ON_CALL:-}" ]; then
      read_count="$(cat "$ACTIVE_GENERATION_READ_COUNT" 2>/dev/null || printf '0')"
      read_count="$((read_count + 1))"
      printf '%s\n' "$read_count" > "$ACTIVE_GENERATION_READ_COUNT"
      if [ "$read_count" = "$ACTIVE_GENERATION_READ_EMPTY_ON_CALL" ]; then
        exit 0
      fi
    fi
    if [ "${ACTIVE_GENERATION_READ_EMPTY:-0}" = "1" ]; then
      exit 0
    fi
    if [ "${ACTIVE_GENERATION_READ_EMPTY_AFTER_BUILD:-0}" = "1" ] \
      && grep -q '^nix build ' "$COMMAND_LOG"; then
      exit 0
    fi
    cat "$ACTIVE_GENERATION"
    ;;
  nix:build)
    if [ "${NIX_BUILD_FAIL:-0}" = "1" ]; then
      printf 'mock local flake build failed\n' >&2
      exit 1
    fi
    if [ "${NIX_BUILD_EMPTY:-0}" != "1" ]; then
      cat "$BUILT_GENERATION"
    fi
    ;;
  nix-env:--profile)
    expected_profile="$HOME/.local/state/nix/profiles/home-manager"
    test "$2" = "$expected_profile" && test "$3" = "--set" \
      && test "$4" = "$(cat "$BUILT_GENERATION")" || {
      printf 'invalid Home Manager profile update\n' >&2
      exit 1
    }
    printf '%s\n' "$4" > "$ACTIVE_GENERATION"
    ;;
  home-manager:switch) cp "$BUILT_GENERATION" "$ACTIVE_GENERATION" ;;
esac
exit 0
"""
    for name in (
        "bw",
        "bwrap",
        "chezmoi",
        "claude",
        "codex",
        "fd",
        "fzf",
        "gh",
        "git",
        "hermes",
        "home-manager",
        "jq",
        "nix",
        "nix-env",
        "node",
        "npm",
        "openclaw",
        "pi",
        "readlink",
        "rg",
        "ssh",
        "ssh-keygen",
        "update-agent-tools",
        "uv",
    ):
        _write_executable(bin_dir / name, mock)
    _write_executable(temp_root / "bin" / "bw", mock)
    _write_activation_package(generation_a)

    marker = home / ".local" / "state" / "workstation-setup" / "complete"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "\n".join(
            (
                "version=2",
                "dotfiles=https://github.com/faviann/dotfiles.git",
                "fingerprint=2|https://github.com/faviann/dotfiles.git|dotfiles/github-cli-token|"
                f"{source}#workstation|{temp_root}/bin/bw",
                "source_commit=commit-a",
                f"active_generation={generation_a}",
                "completed_at=2026-01-01T00:00:00Z",
                "",
            )
        ),
        encoding="utf-8",
    )
    bw_state.write_text("locked", encoding="utf-8")
    git_commit.write_text("commit-a\n", encoding="utf-8")
    git_dirty.write_text("", encoding="utf-8")
    active_generation.write_text(f"{generation_a}\n", encoding="utf-8")
    built_generation.write_text(f"{generation_a}\n", encoding="utf-8")
    env = os.environ | {
        "COMMAND_LOG": str(command_log),
        "BW_STATE": str(bw_state),
        "GIT_COMMIT": str(git_commit),
        "GIT_DIRTY": str(git_dirty),
        "ACTIVE_GENERATION": str(active_generation),
        "ACTIVE_GENERATION_READ_COUNT": str(active_generation_read_count),
        "BUILT_GENERATION": str(built_generation),
    }
    return home, env


def _run_setup(temp_root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(temp_root / "bin" / "workstation-setup")],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )


def _assert_no_direct_home_manager_activation(commands: str) -> None:
    assert not any(
        line.endswith("/activate --driver-version 1") for line in commands.splitlines()
    )


def _render_setup(temp_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "uv",
            "run",
            "--locked",
            "ansible-playbook",
            "tests/regression/fixtures/workstation_first_login_setup_contract.yml",
            "-e",
            f"temp_root={temp_root}",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_workstation_configuration_freshness_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="workstation-freshness-") as temp_root:
        rendered = _render_setup(Path(temp_root))
        assert rendered.returncode == 0, rendered.stdout

        root = Path(temp_root)
        home, env = _prepare_completed_workstation(root)
        _write_executable(
            home / ".local" / "bin" / "workstation-update", "#!/bin/sh\nexit 0\n"
        )
        (home / ".ansible" / "vault-pass").unlink()
        for name in (
            "id_ed25519",
            "id_ed25519.pub",
            "allowed_signers",
            "known_hosts",
        ):
            (home / ".ssh" / name).unlink()
        freshness_command_logs: list[str] = []
        no_switch_command_logs: list[str] = []

        healthy = _run_setup(root, env)

        assert healthy.returncode == 0, healthy.stderr
        assert healthy.stdout == "workstation-setup: environment healthy.\n"
        healthy_commands = (root / "commands.log").read_text(encoding="utf-8")
        freshness_command_logs.append(healthy_commands)
        no_switch_command_logs.append(healthy_commands)
        assert "git -C " in healthy_commands
        assert " status --porcelain" in healthy_commands
        assert "readlink -f " in healthy_commands
        assert "nix build " not in healthy_commands
        assert "home-manager switch" not in healthy_commands
        _assert_no_direct_home_manager_activation(healthy_commands)

        (root / "commands.log").write_text("", encoding="utf-8")
        failed_status = _run_setup(
            root, env | {"GIT_STATUS_FAIL": "1", "NIX_BUILD_FAIL": "1"}
        )

        assert failed_status.returncode != 0
        assert (
            "failed to build the local Home Manager configuration"
            in failed_status.stderr
        )
        assert "environment healthy" not in failed_status.stdout
        failed_status_commands = (root / "commands.log").read_text(encoding="utf-8")
        freshness_command_logs.append(failed_status_commands)
        assert " status --porcelain" in failed_status_commands
        assert "nix build " in failed_status_commands

        (root / "commands.log").write_text("", encoding="utf-8")
        (root / "git-commit").write_text("commit-b\n", encoding="utf-8")

        result = _run_setup(root, env)

        assert result.returncode == 0, result.stderr
        assert result.stdout == "workstation-setup: environment healthy.\n"
        commands = (root / "commands.log").read_text(encoding="utf-8")
        freshness_command_logs.append(commands)
        no_switch_command_logs.append(commands)
        assert "nix build --offline " in commands
        assert "home-manager switch" not in commands
        _assert_no_direct_home_manager_activation(commands)
        marker = (
            home / ".local" / "state" / "workstation-setup" / "complete"
        ).read_text(encoding="utf-8")
        assert "source_commit=commit-b\n" in marker
        assert f"active_generation={root / 'nix/store/generation-a'}\n" in marker

        (root / "commands.log").write_text("", encoding="utf-8")
        (root / "git-commit").write_text("commit-c\n", encoding="utf-8")
        generation_c = root / "nix" / "store" / "generation-c"
        _write_activation_package(generation_c)
        (root / "built-generation").write_text(
            f"{generation_c}\n", encoding="utf-8"
        )

        repaired = _run_setup(root, env)

        assert repaired.returncode == 0, repaired.stderr
        assert repaired.stdout == "workstation-setup: environment repaired and ready.\n"
        repaired_commands = (root / "commands.log").read_text(encoding="utf-8")
        freshness_command_logs.append(repaired_commands)
        expected_build_command = (
            "nix build --offline --no-link --print-out-paths "
            f"{home / '.local/share/chezmoi'}#homeConfigurations.workstation.activationPackage"
        )
        assert [
            line
            for line in repaired_commands.splitlines()
            if line.startswith("nix build ")
        ] == [expected_build_command]
        repaired_lines = repaired_commands.splitlines()
        expected_profile_set = (
            "nix-env --profile "
            f"{home / '.local/state/nix/profiles/home-manager'} --set {generation_c}"
        )
        expected_activation = f"{generation_c / 'activate'} --driver-version 1"
        assert expected_profile_set in repaired_lines
        assert expected_activation in repaired_lines
        assert repaired_lines.index(expected_profile_set) < repaired_lines.index(
            expected_activation
        )
        assert (root / "active-generation").read_text(encoding="utf-8") == (
            f"{generation_c}\n"
        )
        assert "home-manager switch" not in repaired_commands
        assert "nix run " not in repaired_commands
        source_pull = f"git -C {home / '.local/share/chezmoi'} pull"
        assert source_pull not in repaired_commands
        assert not any(
            line.startswith(("bw ", "chezmoi "))
            for line in repaired_commands.splitlines()
        )
        repaired_marker = (
            home / ".local" / "state" / "workstation-setup" / "complete"
        ).read_text(encoding="utf-8")
        assert "source_commit=commit-c\n" in repaired_marker
        assert f"active_generation={generation_c}\n" in repaired_marker

        (root / "commands.log").write_text("", encoding="utf-8")
        (root / "active-generation").write_text(
            f"{root / 'nix/store/generation-d'}\n", encoding="utf-8"
        )
        active_generation_mismatch = _run_setup(root, env)
        assert (
            active_generation_mismatch.returncode == 0
        ), active_generation_mismatch.stderr
        active_generation_mismatch_commands = (root / "commands.log").read_text(
            encoding="utf-8"
        )
        freshness_command_logs.append(active_generation_mismatch_commands)
        assert "nix build " in active_generation_mismatch_commands
        assert f"{generation_c / 'activate'} " in active_generation_mismatch_commands
        assert "home-manager switch" not in active_generation_mismatch_commands
        assert "nix run " not in active_generation_mismatch_commands

        (root / "commands.log").write_text("", encoding="utf-8")
        (root / "git-dirty").write_text(" M home.nix\n", encoding="utf-8")
        dirty = _run_setup(root, env)
        assert dirty.returncode == 0, dirty.stderr
        assert dirty.stdout == "workstation-setup: environment healthy.\n"
        dirty_commands = (root / "commands.log").read_text(encoding="utf-8")
        freshness_command_logs.append(dirty_commands)
        no_switch_command_logs.append(dirty_commands)
        assert "nix build " in dirty_commands
        assert "home-manager switch" not in dirty_commands
        _assert_no_direct_home_manager_activation(dirty_commands)
        (root / "git-dirty").write_text("", encoding="utf-8")

        marker_path = home / ".local" / "state" / "workstation-setup" / "complete"
        for missing_field in ("source_commit", "active_generation"):
            marker_lines = marker_path.read_text(encoding="utf-8").splitlines()
            marker_path.write_text(
                "\n".join(
                    line
                    for line in marker_lines
                    if not line.startswith(f"{missing_field}=")
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "commands.log").write_text("", encoding="utf-8")
            healed = _run_setup(root, env)
            assert healed.returncode == 0, healed.stderr
            healed_commands = (root / "commands.log").read_text(encoding="utf-8")
            freshness_command_logs.append(healed_commands)
            no_switch_command_logs.append(healed_commands)
            assert "nix build " in healed_commands
            assert "home-manager switch" not in healed_commands
            _assert_no_direct_home_manager_activation(healed_commands)
            healed_marker = marker_path.read_text(encoding="utf-8")
            assert "source_commit=commit-c\n" in healed_marker
            assert f"active_generation={generation_c}\n" in healed_marker

        complete_marker = marker_path.read_text(encoding="utf-8")
        for marker_source_line, read_failure in (
            (None, {"GIT_COMMIT_READ_FAIL": "1"}),
            ("source_commit=", {"GIT_COMMIT_READ_EMPTY": "1"}),
        ):
            marker_lines = complete_marker.splitlines()
            marker_without_source = [
                line
                for line in marker_lines
                if not line.startswith("source_commit=")
            ]
            if marker_source_line is not None:
                marker_without_source.append(marker_source_line)
            marker_path.write_text(
                "\n".join(marker_without_source) + "\n", encoding="utf-8"
            )
            (root / "commands.log").write_text("", encoding="utf-8")

            failed_source_read = _run_setup(
                root, env | read_failure | {"NIX_BUILD_FAIL": "1"}
            )

            assert failed_source_read.returncode != 0
            assert (
                "failed to build the local Home Manager configuration"
                in failed_source_read.stderr
            )
            assert "environment healthy" not in failed_source_read.stdout
            assert "nix build " in (root / "commands.log").read_text(
                encoding="utf-8"
            )
            freshness_command_logs.append(
                (root / "commands.log").read_text(encoding="utf-8")
            )
            assert marker_path.read_text(encoding="utf-8") == (
                "\n".join(marker_without_source) + "\n"
            )
            marker_path.write_text(complete_marker, encoding="utf-8")

        marker_with_empty_source = "\n".join(
            "source_commit=" if line.startswith("source_commit=") else line
            for line in complete_marker.splitlines()
        ) + "\n"
        marker_path.write_text(marker_with_empty_source, encoding="utf-8")
        (root / "commands.log").write_text("", encoding="utf-8")

        empty_source_after_build = _run_setup(
            root, env | {"GIT_COMMIT_READ_EMPTY": "1"}
        )

        assert empty_source_after_build.returncode != 0
        assert (
            "could not read the local dotfiles source commit"
            in empty_source_after_build.stderr
        )
        assert "environment healthy" not in empty_source_after_build.stdout
        assert marker_path.read_text(encoding="utf-8") == marker_with_empty_source
        _assert_no_direct_home_manager_activation(
            (root / "commands.log").read_text(encoding="utf-8")
        )
        freshness_command_logs.append(
            (root / "commands.log").read_text(encoding="utf-8")
        )
        no_switch_command_logs.append(
            (root / "commands.log").read_text(encoding="utf-8")
        )
        marker_path.write_text(complete_marker, encoding="utf-8")

        for marker_generation_line, read_failure in (
            (None, {"ACTIVE_GENERATION_READ_FAIL": "1"}),
            (
                "active_generation=",
                {"ACTIVE_GENERATION_READ_EMPTY": "1"},
            ),
        ):
            marker_lines = complete_marker.splitlines()
            marker_without_generation = [
                line
                for line in marker_lines
                if not line.startswith("active_generation=")
            ]
            if marker_generation_line is not None:
                marker_without_generation.append(marker_generation_line)
            marker_path.write_text(
                "\n".join(marker_without_generation) + "\n", encoding="utf-8"
            )
            (root / "commands.log").write_text("", encoding="utf-8")

            failed_generation_read = _run_setup(
                root, env | read_failure | {"NIX_BUILD_FAIL": "1"}
            )

            assert failed_generation_read.returncode != 0
            assert (
                "failed to build the local Home Manager configuration"
                in failed_generation_read.stderr
            )
            assert "environment healthy" not in failed_generation_read.stdout
            assert "nix build " in (root / "commands.log").read_text(
                encoding="utf-8"
            )
            freshness_command_logs.append(
                (root / "commands.log").read_text(encoding="utf-8")
            )
            assert marker_path.read_text(encoding="utf-8") == (
                "\n".join(marker_without_generation) + "\n"
            )
            marker_path.write_text(complete_marker, encoding="utf-8")

        (root / "commands.log").write_text("", encoding="utf-8")
        marker_before_empty_build = marker_path.read_text(encoding="utf-8")
        (root / "git-commit").write_text("commit-d-empty-build\n", encoding="utf-8")

        empty_build = _run_setup(root, env | {"NIX_BUILD_EMPTY": "1"})

        assert empty_build.returncode != 0
        assert "local Home Manager build returned no generation" in empty_build.stderr
        assert "environment healthy" not in empty_build.stdout
        assert marker_path.read_text(encoding="utf-8") == marker_before_empty_build
        freshness_command_logs.append(
            (root / "commands.log").read_text(encoding="utf-8")
        )

        (root / "commands.log").write_text("", encoding="utf-8")
        marker_before_empty_active_generation = marker_path.read_text(encoding="utf-8")
        (root / "git-commit").write_text("commit-d-empty-active\n", encoding="utf-8")

        empty_active_generation = _run_setup(
            root, env | {"ACTIVE_GENERATION_READ_EMPTY_AFTER_BUILD": "1"}
        )

        assert empty_active_generation.returncode != 0
        assert (
            "could not read the active Home Manager generation"
            in empty_active_generation.stderr
        )
        assert "environment healthy" not in empty_active_generation.stdout
        empty_active_generation_commands = (root / "commands.log").read_text(
            encoding="utf-8"
        )
        assert f"{generation_c / 'activate'} --driver-version 1" not in (
            empty_active_generation_commands.splitlines()
        )
        assert marker_path.read_text(encoding="utf-8") == (
            marker_before_empty_active_generation
        )
        freshness_command_logs.append(empty_active_generation_commands)

        (root / "commands.log").write_text("", encoding="utf-8")
        marker_before_empty_marker_generation = marker_path.read_text(encoding="utf-8")
        (root / "git-commit").write_text("commit-d-empty-marker\n", encoding="utf-8")
        (root / "active-generation-read-count").write_text("0\n", encoding="utf-8")

        empty_marker_generation = _run_setup(
            root, env | {"ACTIVE_GENERATION_READ_EMPTY_ON_CALL": "2"}
        )

        assert empty_marker_generation.returncode != 0
        assert (
            "could not read the active Home Manager generation"
            in empty_marker_generation.stderr
        )
        assert "environment healthy" not in empty_marker_generation.stdout
        empty_marker_generation_commands = (root / "commands.log").read_text(
            encoding="utf-8"
        )
        assert f"{generation_c / 'activate'} --driver-version 1" not in (
            empty_marker_generation_commands.splitlines()
        )
        assert marker_path.read_text(encoding="utf-8") == (
            marker_before_empty_marker_generation
        )
        freshness_command_logs.append(empty_marker_generation_commands)
        no_switch_command_logs.append(empty_marker_generation_commands)

        (root / "commands.log").write_text("", encoding="utf-8")
        (root / "git-commit").write_text("commit-e\n", encoding="utf-8")
        failed_build = _run_setup(root, env | {"NIX_BUILD_FAIL": "1"})
        assert failed_build.returncode != 0
        assert (
            "failed to build the local Home Manager configuration"
            in failed_build.stderr
        )
        assert "environment healthy" not in failed_build.stdout
        failed_commands = (root / "commands.log").read_text(encoding="utf-8")
        freshness_command_logs.append(failed_commands)
        assert "nix build " in failed_commands
        assert "home-manager switch" not in failed_commands

        (root / "commands.log").write_text("", encoding="utf-8")
        (root / "git-dirty").write_text(" M home.nix\n", encoding="utf-8")
        generation_e = root / "nix" / "store" / "generation-e"
        _write_activation_package(generation_e)
        (root / "built-generation").write_text(
            f"{generation_e}\n", encoding="utf-8"
        )

        dirty_repaired = _run_setup(root, env)

        assert dirty_repaired.returncode == 0, dirty_repaired.stderr
        assert (
            dirty_repaired.stdout
            == "workstation-setup: environment repaired and ready.\n"
        )
        dirty_repaired_commands = (root / "commands.log").read_text(
            encoding="utf-8"
        )
        freshness_command_logs.append(dirty_repaired_commands)
        dirty_repaired_lines = dirty_repaired_commands.splitlines()
        expected_dirty_profile_set = (
            "nix-env --profile "
            f"{home / '.local/state/nix/profiles/home-manager'} --set {generation_e}"
        )
        expected_dirty_activation = f"{generation_e / 'activate'} --driver-version 1"
        assert [
            line for line in dirty_repaired_lines if line.startswith("nix-env --profile ")
        ] == [expected_dirty_profile_set]
        assert [
            line
            for line in dirty_repaired_lines
            if line.endswith("/activate --driver-version 1")
        ] == [expected_dirty_activation]
        assert dirty_repaired_lines.index(
            expected_dirty_profile_set
        ) < dirty_repaired_lines.index(expected_dirty_activation)
        assert dirty_repaired_lines.count("nix --version") == 2
        assert (root / "active-generation").read_text(encoding="utf-8") == (
            f"{generation_e}\n"
        )
        dirty_repaired_marker = marker_path.read_text(encoding="utf-8")
        assert "source_commit=commit-e\n" in dirty_repaired_marker
        assert f"active_generation={generation_e}\n" in dirty_repaired_marker
        (root / "git-dirty").write_text("", encoding="utf-8")

        (root / "commands.log").write_text("", encoding="utf-8")
        marker_before_failed_readiness = marker_path.read_text(encoding="utf-8")
        (root / "git-commit").write_text("commit-f\n", encoding="utf-8")
        generation_f = root / "nix" / "store" / "generation-f"
        _write_activation_package(generation_f)
        (root / "built-generation").write_text(
            f"{generation_f}\n", encoding="utf-8"
        )

        failed_readiness = _run_setup(
            root, env | {"ACTIVATION_BREAK_TOOL": "1"}
        )

        assert failed_readiness.returncode != 0
        assert "workstation-update missing or not executable" in failed_readiness.stderr
        assert "environment repaired and ready" not in failed_readiness.stdout
        failed_readiness_commands = (root / "commands.log").read_text(
            encoding="utf-8"
        )
        freshness_command_logs.append(failed_readiness_commands)
        assert [
            line
            for line in failed_readiness_commands.splitlines()
            if line.startswith("nix build ")
        ] == [expected_build_command]
        assert f"{generation_f / 'activate'} " in failed_readiness_commands
        assert "home-manager switch" not in failed_readiness_commands
        assert "nix run " not in failed_readiness_commands
        assert marker_path.read_text(encoding="utf-8") == marker_before_failed_readiness

        for no_switch_commands in no_switch_command_logs:
            _assert_no_direct_home_manager_activation(no_switch_commands)

        all_freshness_commands = "\n".join(freshness_command_logs)
        freshness_command_lines = all_freshness_commands.splitlines()
        assert not any(
            line.startswith(("bw ", "chezmoi ", "gh ", "ssh ", "ssh-keygen "))
            for line in freshness_command_lines
        )
        allowed_freshness_git_commands = {
            f"git -C {home / '.local/share/chezmoi'} rev-parse HEAD",
            f"git -C {home / '.local/share/chezmoi'} status --porcelain",
        }
        assert {
            line for line in freshness_command_lines if line.startswith("git ")
        } <= allowed_freshness_git_commands
        assert not any(
            line.startswith(("nix run ", "nix flake update"))
            for line in freshness_command_lines
        )
        assert {
            line for line in freshness_command_lines if line.startswith("nix build ")
        } == {expected_build_command}
        setup_source = (
            REPO_ROOT
            / "playbooks/roles/config/lxc_workstation_baseline/templates/workstation-setup.sh.j2"
        ).read_text(encoding="utf-8")
        assert "chezmoi status" not in setup_source


def test_workstation_first_login_setup_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="workstation-first-login-setup-") as temp_root:
        result = _render_setup(Path(temp_root))

        assert result.returncode == 0, result.stdout

        root = Path(temp_root)
        home, env = _prepare_completed_workstation(root)
        workstation_update = home / ".local" / "bin" / "workstation-update"
        workstation_update_source = (
            home
            / ".local"
            / "share"
            / "chezmoi"
            / "dot_local"
            / "bin"
            / "executable_workstation-update"
        )
        update_agent_tools = home / ".local" / "bin" / "update-agent-tools"
        update_agent_tools_source = (
            home
            / ".local"
            / "share"
            / "chezmoi"
            / "dot_local"
            / "bin"
            / "executable_update-agent-tools"
        )

        marker = home / ".local" / "state" / "workstation-setup" / "complete"
        marker_contents = marker.read_text(encoding="utf-8")
        marker.unlink()
        incomplete_profile = subprocess.run(
            ["script", "-qec", '. "$PROFILE_HOOK"', "/dev/null"],
            input="n\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env
            | {
                "PROFILE_HOOK": str(root / "etc/profile.d/workstation-setup.sh"),
                "SSH_CONNECTION": "test",
            },
            check=False,
        )
        assert incomplete_profile.returncode == 0
        assert "Workstation setup has not completed. Run workstation-setup now? [y/N]" in (
            incomplete_profile.stdout
        )
        marker.write_text(marker_contents, encoding="utf-8")

        _write_executable(workstation_update_source, "#!/bin/sh\nexit 0\n")
        repaired = _run_setup(root, env)
        assert repaired.returncode == 0, repaired.stderr
        assert workstation_update.is_file() and os.access(workstation_update, os.X_OK)
        assert "workstation-setup: environment repaired and ready." in repaired.stdout
        full_repair_commands = (root / "commands.log").read_text(encoding="utf-8")
        assert "home-manager switch" in full_repair_commands
        assert "gh auth status --hostname github.com" in full_repair_commands
        assert "gh api user --jq .login" in full_repair_commands
        assert "ssh -o BatchMode=yes" in full_repair_commands
        assert "ssh-keygen -F github.com" in full_repair_commands
        assert "git -C " in full_repair_commands
        assert " commit -m workstation setup signing test" in full_repair_commands

        (root / "commands.log").write_text("", encoding="utf-8")
        healthy = _run_setup(root, env)
        assert healthy.returncode == 0, healthy.stderr
        assert "workstation-setup: environment healthy." in healthy.stdout
        healthy_commands = (root / "commands.log").read_text(encoding="utf-8")
        assert "home-manager switch" not in healthy_commands
        assert "bw unlock --raw" not in healthy_commands

        update_agent_tools.unlink()
        _write_executable(update_agent_tools_source, "#!/bin/sh\nexit 0\n")
        repaired_agent_tools = _run_setup(root, env)
        assert repaired_agent_tools.returncode == 0, repaired_agent_tools.stderr
        assert update_agent_tools.is_file() and os.access(update_agent_tools, os.X_OK)
        assert "workstation-setup: environment healthy." not in repaired_agent_tools.stdout
        assert "workstation-setup: environment repaired and ready." in repaired_agent_tools.stdout

        workstation_update.unlink()
        workstation_update_source.unlink()
        (root / "commands.log").write_text("", encoding="utf-8")
        absent_source = _run_setup(root, env)
        assert absent_source.returncode != 0
        assert "Bitwarden is locked" not in absent_source.stderr
        assert "Bitwarden is unauthenticated" not in absent_source.stderr
        assert "environment healthy" not in absent_source.stdout
        assert "environment repaired and ready" not in absent_source.stdout
        absent_source_commands = (root / "commands.log").read_text(encoding="utf-8")
        assert not any(line.startswith("bw ") for line in absent_source_commands.splitlines())

        workstation_update_secret_source = Path(f"{workstation_update_source}.tmpl")
        workstation_update_secret_source.write_text("secret-backed\n", encoding="utf-8")
        (root / "commands.log").write_text("", encoding="utf-8")
        escalated = _run_setup(root, env)
        assert escalated.returncode != 0
        assert "Bitwarden is locked" in escalated.stderr
        assert "environment healthy" not in escalated.stdout
        assert "environment repaired and ready" not in escalated.stdout
        assert "bw unlock --raw" in (root / "commands.log").read_text(encoding="utf-8")

        (root / "bw-state").write_text("unauthenticated", encoding="utf-8")
        (root / "commands.log").write_text("", encoding="utf-8")
        unauthenticated = _run_setup(root, env)
        assert unauthenticated.returncode != 0
        assert "Bitwarden is unauthenticated" in unauthenticated.stderr
        assert "Bitwarden is locked" in unauthenticated.stderr
        assert not workstation_update.exists()
        assert "environment healthy" not in unauthenticated.stdout
        assert "environment repaired and ready" not in unauthenticated.stdout
        unauthenticated_commands = (root / "commands.log").read_text(encoding="utf-8")
        assert "bw login" in unauthenticated_commands
        assert "bw unlock --raw" in unauthenticated_commands

        (root / "commands.log").write_text("", encoding="utf-8")
        missing_checker_profile = subprocess.run(
            ["bash", "-c", f'. "{root / "etc/profile.d/workstation-setup.sh"}"'],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env | {"SSH_CONNECTION": "test"},
            check=False,
        )
        assert missing_checker_profile.returncode == 0
        assert "workstation-update is missing or not executable" in missing_checker_profile.stderr
        assert "Run workstation-setup to repair the workstation" in missing_checker_profile.stderr
        assert (root / "commands.log").read_text(encoding="utf-8") == ""

        skipped_prompt_profile = subprocess.run(
            ["bash", "-c", f'. "{root / "etc/profile.d/workstation-setup.sh"}"'],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env | {"SSH_CONNECTION": "test", "WORKSTATION_SETUP_SKIP": "1"},
            check=False,
        )
        assert skipped_prompt_profile.returncode == 0
        assert "workstation-update is missing or not executable" in skipped_prompt_profile.stderr
        assert "Run workstation-setup to repair the workstation" in skipped_prompt_profile.stderr

        _write_executable(workstation_update, "#!/bin/sh\nexit 0\n")
        (root / "commands.log").write_text("", encoding="utf-8")
        checker_present_profile = subprocess.run(
            ["bash", "-c", f'. "{root / "etc/profile.d/workstation-setup.sh"}"'],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env | {"SSH_CONNECTION": "test"},
            check=False,
        )
        assert checker_present_profile.returncode == 0
        assert checker_present_profile.stdout == ""
        assert checker_present_profile.stderr == ""
        assert "workstation-update is missing or not executable" not in checker_present_profile.stderr
        assert (root / "commands.log").read_text(encoding="utf-8") == ""
