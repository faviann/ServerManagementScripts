import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def write_valid_stack(repository: Path) -> Path:
    stack = repository / "stacks/media/example"
    stack.mkdir(parents=True)
    (stack / "compose.yaml").write_text(
        "services:\n  app:\n    image: example/app:1.2\n",
        encoding="utf-8",
    )
    (stack / "stack.yaml").write_text(
        """\
schema_version: 1
kind: stack
name: example
description: Example application
portability:
  tier: portable-app
  owner: stack
exposure:
  traefik: protected
  homepage_instances: [admin]
updates:
  mode: images
  track: stable
""",
        encoding="utf-8",
    )
    return stack


def run_validate(
    repository: Path,
    identity: str = "stacks/media/example",
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "stack_update_policy",
            "validate",
            "--repository-root",
            str(repository),
            identity,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def test_valid_image_tracked_stack_returns_reusable_versioned_result(tmp_path: Path) -> None:
    write_valid_stack(tmp_path)

    completed = run_validate(tmp_path)

    assert completed.returncode == 0
    assert completed.stderr == "validated stacks/media/example\n"
    assert json.loads(completed.stdout) == {
        "command": "validate",
        "errors": [],
        "result": {
            "host": "media",
            "identity": "stacks/media/example",
            "metadata": {
                "description": "Example application",
                "exposure": {"homepage_instances": ["admin"], "traefik": "protected"},
                "kind": "stack",
                "name": "example",
                "portability": {"owner": "stack", "tier": "portable-app"},
                "schema_version": 1,
            },
            "name": "example",
            "policy": {
                "low_confidence": None,
                "mode": "images",
                "track": "stable",
            },
            "procedure": None,
            "services": {
                "app": {"image": "example/app:1.2", "track": "stable"},
            },
        },
        "schema_version": 1,
        "valid": True,
    }


def test_schema_version_rejects_yaml_boolean_true(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata.replace("schema_version: 1", "schema_version: true"),
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "invalid-metadata",
            "message": "schema_version is required and must be int",
            "path": "stack.yaml.schema_version",
        },
        {
            "code": "invalid-value",
            "message": "only schema_version 1 is supported",
            "path": "stack.yaml.schema_version",
        },
    ]


def test_effective_compose_overrides_and_service_track_exceptions_are_resolved(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    (stack / "compose.override.yaml").write_text(
        "services:\n  worker:\n    image: example/worker:2\n",
        encoding="utf-8",
    )
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + "  services:\n    worker:\n      track: edge\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["result"]["services"] == {
        "app": {"image": "example/app:1.2", "track": "stable"},
        "worker": {"image": "example/worker:2", "track": "edge"},
    }


def test_assisted_low_confidence_policy_is_normalized_for_reuse(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + "  low_confidence: assisted\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["result"]["policy"]["low_confidence"] == "assisted"


@pytest.mark.parametrize("value", ["automatic", "true", "null"])
def test_low_confidence_policy_rejects_values_other_than_assisted(
    tmp_path: Path, value: str
) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + f"  low_confidence: {value}\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "invalid-value",
            "message": "low_confidence must be assisted",
            "path": "stack.yaml.updates.low_confidence",
        }
    ]


@pytest.mark.parametrize("value", ["true", "enabled"])
def test_legacy_strict_policy_is_rejected_as_unsupported(
    tmp_path: Path, value: str
) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + f"  strict: {value}\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "invalid-policy",
            "message": "unsupported policy fields: strict",
            "path": "stack.yaml.updates",
        }
    ]


def test_safe_independent_contract_errors_are_reported_together(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    (stack / "compose.yaml").write_text(
        'services:\n  app:\n    image: example/app:1\n  helper:\n    command: ["true"]\n',
        encoding="utf-8",
    )
    (stack / "stack.yaml").write_text(
        """\
schema_version: 1
kind: stack
name: wrong-name
description: Broken example
portability:
  tier: imaginary
  owner: stack
exposure:
  traefik: elsewhere
  homepage_instances: admin
api_token: abc123
updates:
  mode: images
  services:
    ghost:
      track: stable
    helper:
      track: stable
""",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["schema_version"] == 1
    assert payload["valid"] is False
    assert payload["result"] is None
    assert {error["code"] for error in payload["errors"]} == {
        "folder-name-mismatch",
        "invalid-metadata",
        "invalid-value",
        "missing-track",
        "non-image-service",
        "secret-metadata",
        "unknown-service",
    }
    assert completed.stderr


def test_camel_case_credential_keys_are_rejected_as_secret_metadata(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + "apiToken: placeholder\nclientSecret: placeholder\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert [
        (error["code"], error["path"])
        for error in json.loads(completed.stdout)["errors"]
    ] == [
        ("secret-metadata", "stack.yaml.apiToken"),
        ("secret-metadata", "stack.yaml.clientSecret"),
    ]


def test_missing_update_policy_fails_without_inferring_from_image_tags(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(metadata.split("updates:", 1)[0], encoding="utf-8")

    completed = run_validate(tmp_path)

    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert [error["code"] for error in payload["errors"]] == ["missing-policy"]


def test_missing_stack_metadata_returns_a_versioned_cli_failure(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    (stack / "stack.yaml").unlink()

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout) == {
        "command": "validate",
        "errors": [
            {
                "code": "missing-metadata",
                "message": "stack.yaml is required",
                "path": "stack.yaml",
            }
        ],
        "result": None,
        "schema_version": 1,
        "valid": False,
    }


def test_vendor_update_mode_returns_a_versioned_cli_failure(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata.replace("mode: images", "mode: vendor"), encoding="utf-8"
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == 1
    assert payload["valid"] is False
    assert payload["result"] is None
    assert payload["errors"] == [
        {
            "code": "unsupported-mode",
            "message": "strict validation supports only images mode",
            "path": "stack.yaml.updates.mode",
        }
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("procedure", "{mode: assisted}"),
        ("exclude", "true"),
    ],
)
def test_service_policy_rejects_assistance_and_permanent_exclusion_fields(
    tmp_path: Path, field: str, value: str
) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata
        + f"  services:\n    app:\n      track: stable\n      {field}: {value}\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == 1
    assert payload["valid"] is False
    assert payload["result"] is None
    assert payload["errors"] == [
        {
            "code": "invalid-policy",
            "message": "service policy must contain only track",
            "path": "stack.yaml.updates.services.app",
        }
    ]


def test_non_string_update_keys_return_versioned_diagnostics_without_a_traceback(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    (stack / "stack.yaml").write_text(
        """\
schema_version: 1
kind: stack
name: wrong-name
description: Example application
portability:
  tier: portable-app
  owner: stack
exposure:
  traefik: protected
  homepage_instances: [admin]
updates:
  7: malformed
  mode: images
  track: stable
""",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["schema_version"] == 1
    assert payload["valid"] is False
    assert {error["code"] for error in payload["errors"]} == {
        "folder-name-mismatch",
        "invalid-policy",
    }
    assert "Traceback" not in completed.stderr


def test_assisted_procedure_requires_a_resolvable_stack_local_runbook(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + "  procedure:\n    mode: assisted\n    runbook: docs/upgrade.md\n",
        encoding="utf-8",
    )

    missing = run_validate(tmp_path)
    (stack / "docs").mkdir()
    (stack / "docs/upgrade.md").write_text("# Upgrade\n", encoding="utf-8")
    valid = run_validate(tmp_path)

    assert missing.returncode == 1
    assert {error["code"] for error in json.loads(missing.stdout)["errors"]} == {"invalid-runbook"}
    assert valid.returncode == 0
    assert json.loads(valid.stdout)["result"]["procedure"] == {
        "mode": "assisted",
        "runbook": "docs/upgrade.md",
    }


def test_assisted_procedure_preserves_a_markdown_fragment_on_a_local_runbook(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + "  procedure:\n    mode: assisted\n    runbook: README.md#updating\n",
        encoding="utf-8",
    )
    (stack / "README.md").write_text("# Example\n\n## Updating\n", encoding="utf-8")

    completed = run_validate(tmp_path)

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["result"]["procedure"] == {
        "mode": "assisted",
        "runbook": "README.md#updating",
    }


def test_assisted_procedure_rejects_a_missing_markdown_fragment_target(
    tmp_path: Path,
) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + "  procedure:\n    mode: assisted\n    runbook: README.md#updating\n",
        encoding="utf-8",
    )
    (stack / "README.md").write_text("# Example\n", encoding="utf-8")

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "invalid-runbook",
            "message": "runbook fragment does not resolve to a Markdown target",
            "path": "stack.yaml.updates.procedure.runbook",
        }
    ]


def test_assisted_procedure_resolves_an_explicit_markdown_anchor(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + "  procedure:\n    mode: assisted\n    runbook: README.md#upgrade-notes\n",
        encoding="utf-8",
    )
    (stack / "README.md").write_text(
        '<a id="upgrade-notes"></a>\n\nUpgrade details.\n', encoding="utf-8"
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["result"]["procedure"] == {
        "mode": "assisted",
        "runbook": "README.md#upgrade-notes",
    }


def test_assisted_procedure_rejects_an_empty_markdown_fragment(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + "  procedure:\n    mode: assisted\n    runbook: README.md#\n",
        encoding="utf-8",
    )
    (stack / "README.md").write_text("# Example\n", encoding="utf-8")

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert [error["code"] for error in json.loads(completed.stdout)["errors"]] == [
        "invalid-runbook"
    ]


def test_assisted_procedure_rejects_an_invalid_markdown_fragment(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + "  procedure:\n    mode: assisted\n    runbook: README.md#updating section\n",
        encoding="utf-8",
    )
    (stack / "README.md").write_text("# Example\n", encoding="utf-8")

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert [error["code"] for error in json.loads(completed.stdout)["errors"]] == [
        "invalid-runbook"
    ]


def test_malformed_metadata_and_compose_failures_are_structured(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    (stack / "stack.yaml").write_text("updates: [\n", encoding="utf-8")
    (stack / "compose.yaml").write_text("services: [\n", encoding="utf-8")

    completed = run_validate(tmp_path)

    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert {error["code"] for error in payload["errors"]} == {
        "compose-resolution",
        "malformed-metadata",
    }


def test_stack_selection_rejects_a_stack_root_symlink_outside_the_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "stack.yaml").write_text("updates: [\n", encoding="utf-8")
    (outside / "compose.yaml").write_text("services: [\n", encoding="utf-8")
    (repository / "stacks/media").mkdir(parents=True)
    (repository / "stacks/media/example").symlink_to(outside, target_is_directory=True)

    completed = run_validate(repository)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "invalid-identity",
            "message": "selected stack directory must stay within the repository",
            "path": "identity",
        }
    ]
    assert "Traceback" not in completed.stderr


def test_validation_only_resolves_compose_and_cannot_mutate_github_or_deployed_state(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    stack = write_valid_stack(repository)
    real_git = shutil.which("git")
    assert real_git is not None
    subprocess.run([real_git, "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        [real_git, "config", "user.name", "Policy Test"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        [real_git, "config", "user.email", "policy@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run([real_git, "add", "."], cwd=repository, check=True)
    subprocess.run(
        [real_git, "commit", "-qm", "fixture"], cwd=repository, check=True
    )
    control = tmp_path / "control"
    fake_bin = control / "bin"
    fake_bin.mkdir(parents=True)
    command_log = control / "commands.log"
    tripwire_log = control / "tripwire.log"
    deployed_sentinel = control / "deployed-state"
    deployed_sentinel.write_text("unchanged\n", encoding="utf-8")
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" >> "$COMMAND_LOG"
if [ "$#" -eq 8 ] && [ "$1" = compose ] && [ "$2" = -f ] \\
   && [ "$3" = "$EXPECTED_COMPOSE" ] && [ "$4" = config ] \\
   && [ "$5" = --format ] && [ "$6" = json ] \\
   && [ "$7" = --no-interpolate ] && [ "$8" = --no-env-resolution ]; then
  printf '%s\\n' '{"services":{"app":{"image":"example/app:1.2"}}}'
  exit 0
fi
printf '%s\\n' "docker $*" >> "$TRIPWIRE_LOG"
printf '%s\\n' mutated > "$DEPLOYED_SENTINEL"
exit 97
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    tripwire = """#!/bin/sh
printf '%s\\n' "$0 $*" >> "$TRIPWIRE_LOG"
printf '%s\\n' mutated > "$DEPLOYED_SENTINEL"
exit 97
"""
    for executable in ("gh", "git", "ansible", "ansible-playbook", "ssh", "pct"):
        path = fake_bin / executable
        path.write_text(tripwire, encoding="utf-8")
        path.chmod(0o755)

    def repository_digest() -> str:
        return hashlib.sha256(
            b"".join(
                path.relative_to(repository).as_posix().encode()
                + b"\0"
                + path.read_bytes()
                for path in sorted(repository.rglob("*"))
                if path.is_file()
                and ".git" not in path.relative_to(repository).parts
            )
        ).hexdigest()

    before_bytes = repository_digest()
    before_head = subprocess.check_output(
        [real_git, "rev-parse", "HEAD"], cwd=repository, text=True
    )
    before_status = subprocess.check_output(
        [real_git, "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repository,
        text=True,
    )
    before_deployed_state = deployed_sentinel.read_bytes()
    environment = os.environ.copy()
    environment.update(
        {
            "COMMAND_LOG": str(command_log),
            "DEPLOYED_SENTINEL": str(deployed_sentinel),
            "EXPECTED_COMPOSE": str(stack / "compose.yaml"),
            "PATH": str(fake_bin),
            "TRIPWIRE_LOG": str(tripwire_log),
        }
    )

    completed = run_validate(repository, env=environment)

    assert completed.returncode == 0
    assert repository_digest() == before_bytes
    assert (
        subprocess.check_output(
            [real_git, "rev-parse", "HEAD"], cwd=repository, text=True
        )
        == before_head
    )
    assert (
        subprocess.check_output(
            [real_git, "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repository,
            text=True,
        )
        == before_status
    )
    assert deployed_sentinel.read_bytes() == before_deployed_state
    assert not tripwire_log.exists()
    assert command_log.read_text(encoding="utf-8").splitlines() == [
        "compose -f "
        + str(stack / "compose.yaml")
        + " config --format json --no-interpolate --no-env-resolution"
    ]
