import json
import hashlib
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


def run_validate(repository: Path, identity: str = "stacks/media/example") -> subprocess.CompletedProcess[str]:
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
    (stack / "README.md").write_text("# Example\n", encoding="utf-8")

    completed = run_validate(tmp_path)

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["result"]["procedure"] == {
        "mode": "assisted",
        "runbook": "README.md#updating",
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


def test_validation_does_not_change_repository_bytes_or_git_state(tmp_path: Path) -> None:
    write_valid_stack(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Policy Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "policy@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)

    before_bytes = hashlib.sha256(
        b"".join(path.relative_to(tmp_path).as_posix().encode() + b"\0" + path.read_bytes() for path in sorted(tmp_path.glob("stacks/**/*")) if path.is_file())
    ).hexdigest()
    before_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True)
    before_status = subprocess.check_output(["git", "status", "--porcelain"], cwd=tmp_path, text=True)

    completed = run_validate(tmp_path)

    after_bytes = hashlib.sha256(
        b"".join(path.relative_to(tmp_path).as_posix().encode() + b"\0" + path.read_bytes() for path in sorted(tmp_path.glob("stacks/**/*")) if path.is_file())
    ).hexdigest()
    assert completed.returncode == 0
    assert after_bytes == before_bytes
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True) == before_head
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=tmp_path, text=True) == before_status
