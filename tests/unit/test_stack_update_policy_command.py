import base64
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
        ("secret-metadata", "stack.yaml.<secret-key-1>"),
        ("secret-metadata", "stack.yaml.<secret-key-2>"),
    ]


@pytest.mark.parametrize(
    "header",
    [
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----",
        "-----BEGIN DSA PRIVATE KEY-----",
        "-----BEGIN PGP PRIVATE KEY BLOCK-----",
    ],
)
def test_private_key_block_headers_in_neutral_fields_are_rejected_without_leaking(
    tmp_path: Path, header: str
) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + f"notes: {json.dumps(header)}\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "secret-metadata",
            "message": "secret-shaped metadata is forbidden",
            "path": "stack.yaml.notes",
        }
    ]
    assert header not in completed.stdout
    assert header not in completed.stderr


@pytest.mark.parametrize("field", ["credential", "password", "token"])
def test_secret_named_fields_are_rejected_without_leaking_their_values(
    tmp_path: Path, field: str
) -> None:
    stack = write_valid_stack(tmp_path)
    marker = "fixture-value-that-must-stay-private"
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + f"{field}: {marker}\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "secret-metadata",
            "message": "secret-shaped metadata is forbidden",
            "path": "stack.yaml.<secret-key-1>",
        }
    ]
    assert marker not in completed.stdout
    assert marker not in completed.stderr


def test_secret_shaped_mapping_key_is_rejected_without_leaking_the_key(
    tmp_path: Path,
) -> None:
    stack = write_valid_stack(tmp_path)
    marker = "fixture-secret-key-marker-that-must-stay-private"
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + f'{json.dumps(f"apiToken-{marker}")}: placeholder\n',
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "secret-metadata",
            "message": "secret-shaped metadata is forbidden",
            "path": "stack.yaml.<secret-key-1>",
        }
    ]
    assert marker not in completed.stdout
    assert marker not in completed.stderr


@pytest.mark.parametrize(
    "secret_shape",
    [
        "{{ vault_fixture_reference }}",
        "password=<REPLACE_ME>",
        "token=<REPLACE_ME>",
    ],
)
def test_secret_shaped_values_are_rejected_without_leaking(
    tmp_path: Path, secret_shape: str
) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + f"notes: {json.dumps(secret_shape)}\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "secret-metadata",
            "message": "secret-shaped metadata is forbidden",
            "path": "stack.yaml.notes",
        }
    ]
    assert secret_shape not in completed.stdout
    assert secret_shape not in completed.stderr


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
        "invalid-metadata",
        "invalid-policy",
    }
    assert "Traceback" not in completed.stderr


def test_non_string_update_keys_are_redacted_from_cli_diagnostics(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    marker = "fixture-binary-update-key-that-must-not-be-echoed"
    encoded_marker = base64.b64encode(marker.encode()).decode()
    (stack / "stack.yaml").write_text(
        f"""\
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
  7: malformed
  !!binary {encoded_marker}: malformed
""",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    invalid_policy = next(
        error
        for error in json.loads(completed.stdout)["errors"]
        if error["code"] == "invalid-policy"
    )
    assert invalid_policy == {
        "code": "invalid-policy",
        "message": (
            "unsupported policy fields: <non-string-key>, <non-string-key>"
        ),
        "path": "stack.yaml.updates",
    }
    assert marker not in completed.stdout
    assert marker not in completed.stderr


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


def test_assisted_procedure_resolves_a_setext_heading_fragment(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + "  procedure:\n    mode: assisted\n    runbook: README.md#updating\n",
        encoding="utf-8",
    )
    (stack / "README.md").write_text(
        "Example\n=======\n\nUpdating\n--------\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["result"]["procedure"] == {
        "mode": "assisted",
        "runbook": "README.md#updating",
    }


def test_assisted_procedure_resolves_a_duplicate_heading_suffix(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + "  procedure:\n    mode: assisted\n    runbook: README.md#updating-1\n",
        encoding="utf-8",
    )
    (stack / "README.md").write_text(
        "# Updating\n\nFirst.\n\n## Updating\n\nSecond.\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["result"]["procedure"] == {
        "mode": "assisted",
        "runbook": "README.md#updating-1",
    }


def test_assisted_procedure_rejects_parent_directory_traversal(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    (stack / "docs").mkdir()
    (stack / "README.md").write_text("# Updating\n", encoding="utf-8")
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata
        + "  procedure:\n    mode: assisted\n    runbook: docs/../README.md#updating\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "invalid-runbook",
            "message": "runbook must stay within the selected stack",
            "path": "stack.yaml.updates.procedure.runbook",
        }
    ]


def test_assisted_procedure_rejects_a_symlink_escape_to_repository_docs(
    tmp_path: Path,
) -> None:
    stack = write_valid_stack(tmp_path)
    repository_docs = tmp_path / "docs"
    repository_docs.mkdir()
    (repository_docs / "upgrade.md").write_text("# Updating\n", encoding="utf-8")
    (stack / "linked-docs").symlink_to(repository_docs, target_is_directory=True)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata
        + "  procedure:\n    mode: assisted\n    runbook: linked-docs/upgrade.md#updating\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "invalid-runbook",
            "message": "runbook must stay within the selected stack",
            "path": "stack.yaml.updates.procedure.runbook",
        }
    ]


def test_assisted_procedure_symlink_loop_returns_a_versioned_cli_failure(
    tmp_path: Path,
) -> None:
    stack = write_valid_stack(tmp_path)
    (stack / "RUNBOOK.md").symlink_to("RUNBOOK.md")
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata
        + "  procedure:\n"
        + "    mode: assisted\n"
        + "    runbook: RUNBOOK.md\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "invalid-runbook",
            "message": "runbook path could not be resolved",
            "path": "stack.yaml.updates.procedure.runbook",
        }
    ]
    assert "Traceback" not in completed.stderr


def test_assisted_procedure_nul_path_returns_a_versioned_cli_failure(
    tmp_path: Path,
) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata
        + "  procedure:\n"
        + "    mode: assisted\n"
        + '    runbook: "bad\\0path"\n',
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout) == {
        "command": "validate",
        "errors": [
            {
                "code": "invalid-runbook",
                "message": "runbook path could not be resolved",
                "path": "stack.yaml.updates.procedure.runbook",
            }
        ],
        "result": None,
        "schema_version": 1,
        "valid": False,
    }
    assert "bad" not in completed.stderr
    assert "Traceback" not in completed.stderr


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


def test_assisted_procedure_rejects_an_anchor_inside_an_html_comment(
    tmp_path: Path,
) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata
        + "  procedure:\n"
        + "    mode: assisted\n"
        + "    runbook: README.md#upgrade-notes\n",
        encoding="utf-8",
    )
    (stack / "README.md").write_text(
        "<!--\n<a id=\"upgrade-notes\"></a>\n-->\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "invalid-runbook",
            "message": "runbook fragment does not resolve to a Markdown target",
            "path": "stack.yaml.updates.procedure.runbook",
        }
    ]


def test_assisted_procedure_ignores_comment_markup_inside_a_fenced_block(
    tmp_path: Path,
) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + "  procedure:\n    mode: assisted\n    runbook: README.md#updating\n",
        encoding="utf-8",
    )
    (stack / "README.md").write_text(
        "```markdown\n<!--\n## Not a target\n```\n\n## Updating\n",
        encoding="utf-8",
    )

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


def test_compose_failure_diagnostics_do_not_echo_docker_stderr(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = "fixture-compose-stderr-that-must-not-be-echoed"
    docker = fake_bin / "docker"
    docker.write_text(
        f"#!/bin/sh\nprintf '%s\\n' '{marker}' >&2\nexit 1\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

    completed = run_validate(tmp_path, env=environment)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "compose-resolution",
            "message": "Docker Compose could not resolve the stack definition",
            "path": "compose",
        }
    ]
    assert marker not in completed.stdout
    assert marker not in completed.stderr


def test_malformed_metadata_diagnostics_do_not_echo_yaml_input(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    secret_marker = "fixture-value-that-must-not-be-echoed"
    (stack / "stack.yaml").write_text(
        f"description: [{secret_marker}\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["errors"][0] == {
        "code": "malformed-metadata",
        "message": "could not parse stack.yaml at line 2, column 1",
        "path": "stack.yaml",
    }
    assert secret_marker not in completed.stdout
    assert secret_marker not in completed.stderr


def test_recursive_yaml_alias_returns_a_controlled_versioned_failure(tmp_path: Path) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + "extension: &recursive [*recursive]\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["schema_version"] == 1
    assert payload["valid"] is False
    assert payload["result"] is None
    assert {
        (error["code"], error["path"], error["message"])
        for error in payload["errors"]
    } == {
        (
            "malformed-metadata",
            "stack.yaml.extension[0]",
            "recursive YAML aliases are not supported",
        )
    }
    assert "Traceback" not in completed.stderr


def test_date_valued_optional_exposure_metadata_returns_a_versioned_failure(
    tmp_path: Path,
) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata.replace(
            "  homepage_instances: [admin]\n",
            "  homepage_instances: [admin]\n  homepage_group: 2026-08-12\n",
        ),
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "invalid-metadata",
            "message": "metadata value must be JSON-compatible",
            "path": "stack.yaml.exposure.homepage_group",
        }
    ]
    assert "Traceback" not in completed.stderr


def test_non_json_yaml_metadata_types_are_aggregated_as_versioned_failures(
    tmp_path: Path,
) -> None:
    stack = write_valid_stack(tmp_path)
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata
        + """\
extension:
  binary: !!binary Zml4dHVyZQ==
  choices: !!set {one: null, two: null}
  keyed: {7: value}
  non_finite: .inf
""",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "invalid-metadata",
            "message": "metadata value must be JSON-compatible",
            "path": "stack.yaml.extension.binary",
        },
        {
            "code": "invalid-metadata",
            "message": "metadata value must be JSON-compatible",
            "path": "stack.yaml.extension.choices",
        },
        {
            "code": "invalid-metadata",
            "message": "metadata mapping keys must be strings",
            "path": "stack.yaml.extension.keyed",
        },
        {
            "code": "invalid-metadata",
            "message": "metadata value must be JSON-compatible",
            "path": "stack.yaml.extension.non_finite",
        },
    ]
    assert "Traceback" not in completed.stderr


def test_hyphenated_vault_pass_path_in_neutral_metadata_is_rejected_without_leaking(
    tmp_path: Path,
) -> None:
    stack = write_valid_stack(tmp_path)
    vault_reference = "~/.ansible/vault-pass"
    metadata = (stack / "stack.yaml").read_text(encoding="utf-8")
    (stack / "stack.yaml").write_text(
        metadata + f"notes: {json.dumps(vault_reference)}\n",
        encoding="utf-8",
    )

    completed = run_validate(tmp_path)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["errors"] == [
        {
            "code": "secret-metadata",
            "message": "secret-shaped metadata is forbidden",
            "path": "stack.yaml.notes",
        }
    ]
    assert vault_reference not in completed.stdout
    assert vault_reference not in completed.stderr


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


def test_stack_selection_symlink_loop_returns_a_versioned_cli_failure(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    stack_parent = repository / "stacks/media"
    stack_parent.mkdir(parents=True)
    (stack_parent / "example").symlink_to("example", target_is_directory=True)

    completed = run_validate(repository)

    assert completed.returncode == 1
    assert json.loads(completed.stdout) == {
        "command": "validate",
        "errors": [
            {
                "code": "invalid-identity",
                "message": "selected stack directory could not be resolved",
                "path": "identity",
            }
        ],
        "result": None,
        "schema_version": 1,
        "valid": False,
    }
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
