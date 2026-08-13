import json
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from stack_update_policy import (  # noqa: E402
    ArtifactError,
    load_artifact,
    validate_artifact,
    write_draft_artifact,
    write_final_artifact,
)
from stack_update_policy import artifacts as artifacts_module  # noqa: E402


CREATED = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
FINGERPRINTS = {"stacks/media/example": "1" * 64}
EVIDENCE = [
    {
        "id": "example-app-update",
        "stack": "stacks/media/example",
        "service": "app",
        "current_image": "example/app:1",
        "candidate_image": "example/app:2",
        "reason": "stable-track update",
    }
]


def test_draft_artifact_has_deterministic_bytes_and_checksum(tmp_path: Path) -> None:
    destination = tmp_path / "draft.json"

    checksum = write_draft_artifact(
        destination,
        repository="faviann/homelab-iac",
        source_commit="a" * 40,
        input_fingerprints=FINGERPRINTS,
        evidence=EVIDENCE,
        assessment_requests=[
            {
                "stack": "stacks/media/example",
                "question": "Does the documented migration still apply?",
                "evidence_ids": ["example-app-update"],
            }
        ],
        now=CREATED,
    )

    assert checksum == (
        "e81f950b9528afb68003b9b88c05ced306be92823b658950f6a4180c6efb87e9"
    )
    assert json.loads(destination.read_bytes()) == {
        "artifact_kind": "draft",
        "assessment_requests": [
            {
                "evidence_ids": ["example-app-update"],
                "question": "Does the documented migration still apply?",
                "stack": "stacks/media/example",
            }
        ],
        "created_at": "2026-08-13T12:00:00Z",
        "evidence": EVIDENCE,
        "expires_at": "2026-08-14T12:00:00Z",
        "input_fingerprints": FINGERPRINTS,
        "repository": "faviann/homelab-iac",
        "schema_version": 1,
        "source_commit": "a" * 40,
    }
    assert destination.read_bytes().endswith(b"\n")


def write_fixture(path: Path) -> str:
    return write_final_artifact(
        path,
        repository="faviann/homelab-iac",
        source_commit="a" * 40,
        input_fingerprints=FINGERPRINTS,
        evidence=EVIDENCE,
        publish_actions=[
            {
                "kind": "create_issue",
                "title": "Update example/app to 2",
                "body": "<!-- managed:image-update -->\nExact managed Markdown\n",
            },
            {
                "kind": "create_comment",
                "issue_number": 42,
                "body": "<!-- managed:image-update -->\nExact managed comment\n",
            },
        ],
        now=CREATED,
    )


def write_artifact_for_kind(
    path: Path, artifact_kind: str, **overrides: object
) -> str:
    arguments = {
        "repository": "faviann/homelab-iac",
        "source_commit": "a" * 40,
        "input_fingerprints": FINGERPRINTS,
        "evidence": EVIDENCE,
        "now": CREATED,
    }
    arguments.update(overrides)
    if artifact_kind == "draft":
        return write_draft_artifact(
            path,
            assessment_requests=[
                {
                    "stack": "stacks/media/example",
                    "question": "Assess migration",
                    "evidence_ids": ["example-app-update"],
                }
            ],
            **arguments,
        )
    return write_final_artifact(
        path,
        publish_actions=[
            {"kind": "create_issue", "title": "Update", "body": "Managed body"}
        ],
        **arguments,
    )


def test_final_artifact_contains_only_exact_publishable_actions(tmp_path: Path) -> None:
    destination = tmp_path / "final.json"

    checksum = write_fixture(destination)
    artifact = load_artifact(
        destination,
        expected_checksum=checksum,
        expected_kind="final",
        repository="faviann/homelab-iac",
        source_commit="a" * 40,
        input_fingerprints=FINGERPRINTS,
        now=CREATED,
    )

    assert artifact["artifact_kind"] == "final"
    assert "assessment_requests" not in artifact
    assert artifact["publish_actions"][0]["body"].endswith("Exact managed Markdown\n")


@pytest.mark.parametrize("artifact_kind", ["draft", "final"])
def test_writers_reject_repository_with_terminal_newline(
    tmp_path: Path, artifact_kind: str
) -> None:
    destination = tmp_path / f"{artifact_kind}.json"

    with pytest.raises(ArtifactError, match="does not match schema"):
        write_artifact_for_kind(
            destination,
            artifact_kind,
            repository="faviann/homelab-iac\n",
        )

    assert not destination.exists()


@pytest.mark.parametrize("artifact_kind", ["draft", "final"])
def test_writers_reject_source_commit_with_terminal_newline(
    tmp_path: Path, artifact_kind: str
) -> None:
    destination = tmp_path / f"{artifact_kind}.json"

    with pytest.raises(ArtifactError, match="does not match schema"):
        write_artifact_for_kind(
            destination,
            artifact_kind,
            source_commit=("a" * 40) + "\n",
        )

    assert not destination.exists()


@pytest.mark.parametrize("artifact_kind", ["draft", "final"])
def test_writers_reject_fingerprint_with_terminal_newline(
    tmp_path: Path, artifact_kind: str
) -> None:
    destination = tmp_path / f"{artifact_kind}.json"

    with pytest.raises(ArtifactError, match="does not match schema"):
        write_artifact_for_kind(
            destination,
            artifact_kind,
            input_fingerprints={"stacks/media/example": ("1" * 64) + "\n"},
        )

    assert not destination.exists()


@pytest.mark.parametrize("artifact_kind", ["draft", "final"])
def test_writers_reject_fingerprint_stack_key_with_terminal_newline(
    tmp_path: Path, artifact_kind: str
) -> None:
    destination = tmp_path / f"{artifact_kind}.json"

    with pytest.raises(ArtifactError, match="does not match schema"):
        write_artifact_for_kind(
            destination,
            artifact_kind,
            input_fingerprints={"stacks/media/example\n": "1" * 64},
        )

    assert not destination.exists()


@pytest.mark.parametrize("artifact_kind", ["draft", "final"])
def test_writers_reject_evidence_stack_with_terminal_newline(
    tmp_path: Path, artifact_kind: str
) -> None:
    destination = tmp_path / f"{artifact_kind}.json"
    evidence = [dict(EVIDENCE[0], stack="stacks/media/example\n")]

    with pytest.raises(ArtifactError, match="does not match schema"):
        write_artifact_for_kind(
            destination,
            artifact_kind,
            evidence=evidence,
        )

    assert not destination.exists()


def test_draft_writer_rejects_assessment_stack_with_terminal_newline(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "draft.json"

    with pytest.raises(ArtifactError, match="does not match schema"):
        write_draft_artifact(
            destination,
            repository="faviann/homelab-iac",
            source_commit="a" * 40,
            input_fingerprints=FINGERPRINTS,
            evidence=EVIDENCE,
            assessment_requests=[
                {
                    "stack": "stacks/media/example\n",
                    "question": "Assess migration",
                    "evidence_ids": ["example-app-update"],
                }
            ],
            now=CREATED,
        )

    assert not destination.exists()


def test_consumer_rejects_exact_expiry_boundary(tmp_path: Path) -> None:
    destination = tmp_path / "final.json"
    checksum = write_fixture(destination)

    load_artifact(
        destination,
        expected_checksum=checksum,
        expected_kind="final",
        repository="faviann/homelab-iac",
        source_commit="a" * 40,
        input_fingerprints=FINGERPRINTS,
        now=datetime(2026, 8, 14, 11, 59, 59, tzinfo=UTC),
    )
    with pytest.raises(ArtifactError, match="expired"):
        load_artifact(
            destination,
            expected_checksum=checksum,
            expected_kind="final",
            repository="faviann/homelab-iac",
            source_commit="a" * 40,
            input_fingerprints=FINGERPRINTS,
            now=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
        )


def test_consumer_requires_exact_checksum_before_parsing(tmp_path: Path) -> None:
    destination = tmp_path / "final.json"
    checksum = write_fixture(destination)
    destination.write_bytes(destination.read_bytes() + b" ")

    with pytest.raises(ArtifactError, match="checksum"):
        load_artifact(
            destination,
            expected_checksum=checksum,
            expected_kind="final",
            repository="faviann/homelab-iac",
            source_commit="a" * 40,
            input_fingerprints=FINGERPRINTS,
            now=CREATED,
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"repository": "someone/else"}, "repository"),
        ({"source_commit": "b" * 40}, "source commit"),
        ({"input_fingerprints": {"stacks/media/example": "2" * 64}}, "relevant inputs"),
    ],
)
def test_consumer_rejects_context_mismatch(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    destination = tmp_path / "final.json"
    checksum = write_fixture(destination)
    arguments = {
        "repository": "faviann/homelab-iac",
        "source_commit": "a" * 40,
        "input_fingerprints": FINGERPRINTS,
    }
    arguments.update(overrides)

    with pytest.raises(ArtifactError, match=message):
        load_artifact(
            destination,
            expected_checksum=checksum,
            expected_kind="final",
            now=CREATED,
            **arguments,
        )


def test_atomic_write_failure_leaves_no_complete_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "final.json"

    def fail_replace(source: object, target: object) -> None:
        raise OSError("injected atomic replacement failure")

    monkeypatch.setattr(artifacts_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        write_fixture(destination)

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_consumer_rejects_unknown_schema_version_after_checksum_match(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "final.json"
    write_fixture(destination)
    artifact = json.loads(destination.read_bytes())
    artifact["schema_version"] = 2
    encoded = json.dumps(
        artifact, sort_keys=True, separators=(",", ":")
    ).encode() + b"\n"
    destination.write_bytes(encoded)

    with pytest.raises(ArtifactError, match="schema version"):
        load_artifact(
            destination,
            expected_checksum=hashlib.sha256(encoded).hexdigest(),
            expected_kind="final",
            repository="faviann/homelab-iac",
            source_commit="a" * 40,
            input_fingerprints=FINGERPRINTS,
            now=CREATED,
        )


def test_consumer_rejects_non_rfc3339_timestamp_after_checksum_match(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "final.json"
    write_fixture(destination)
    artifact = json.loads(destination.read_bytes())
    artifact["created_at"] = "2026-08-13 12:00:00Z"
    encoded = json.dumps(
        artifact, sort_keys=True, separators=(",", ":")
    ).encode() + b"\n"
    destination.write_bytes(encoded)

    with pytest.raises(ArtifactError, match="does not match schema"):
        load_artifact(
            destination,
            expected_checksum=hashlib.sha256(encoded).hexdigest(),
            expected_kind="final",
            repository="faviann/homelab-iac",
            source_commit="a" * 40,
            input_fingerprints=FINGERPRINTS,
            now=CREATED,
        )


def test_consumer_rejects_artifact_of_wrong_lifecycle_kind(tmp_path: Path) -> None:
    destination = tmp_path / "final.json"
    checksum = write_fixture(destination)

    with pytest.raises(ArtifactError, match="kind"):
        load_artifact(
            destination,
            expected_checksum=checksum,
            expected_kind="draft",
            repository="faviann/homelab-iac",
            source_commit="a" * 40,
            input_fingerprints=FINGERPRINTS,
            now=CREATED,
        )


@pytest.mark.parametrize(
    "sensitive_value",
    [
        "token=top-secret-value",
        "-----BEGIN PRIVATE KEY-----",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
    ],
)
def test_artifacts_reject_secret_values_in_structured_output(
    tmp_path: Path, sensitive_value: str
) -> None:
    destination = tmp_path / "final.json"

    with pytest.raises(ArtifactError, match="sensitive material"):
        write_final_artifact(
            destination,
            repository="faviann/homelab-iac",
            source_commit="a" * 40,
            input_fingerprints=FINGERPRINTS,
            evidence=EVIDENCE,
            publish_actions=[
                {"kind": "create_issue", "title": "Update", "body": sensitive_value}
            ],
            now=CREATED,
        )

    assert not destination.exists()


@pytest.mark.parametrize("artifact_kind", ["draft", "final"])
def test_writers_reject_bearer_authorization_material(
    tmp_path: Path, artifact_kind: str
) -> None:
    destination = tmp_path / f"{artifact_kind}.json"
    evidence = [dict(EVIDENCE[0], reason="Authorization: Bearer abc.def-123")]

    with pytest.raises(ArtifactError, match="sensitive material"):
        write_artifact_for_kind(
            destination,
            artifact_kind,
            evidence=evidence,
        )

    assert not destination.exists()


@pytest.mark.parametrize("artifact_kind", ["draft", "final"])
def test_writers_reject_temporary_scan_paths(
    tmp_path: Path, artifact_kind: str
) -> None:
    destination = tmp_path / f"{artifact_kind}.json"
    evidence = [dict(EVIDENCE[0], reason="scan path=/tmp/renovate-scan/output.json")]

    with pytest.raises(ArtifactError, match="sensitive material"):
        write_artifact_for_kind(
            destination,
            artifact_kind,
            evidence=evidence,
        )

    assert not destination.exists()


@pytest.mark.parametrize("artifact_kind", ["draft", "final"])
def test_writers_reject_raw_renovate_log_records(
    tmp_path: Path, artifact_kind: str
) -> None:
    destination = tmp_path / f"{artifact_kind}.json"
    raw_log = '{"level":20,"msg":"packageFiles with updates","config":{}}'
    evidence = [dict(EVIDENCE[0], reason=raw_log)]

    with pytest.raises(ArtifactError, match="sensitive material"):
        write_artifact_for_kind(
            destination,
            artifact_kind,
            evidence=evidence,
        )

    assert not destination.exists()


def test_validation_rejects_raw_logs_and_temporary_scan_material(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "draft.json"
    write_draft_artifact(
        destination,
        repository="faviann/homelab-iac",
        source_commit="a" * 40,
        input_fingerprints=FINGERPRINTS,
        evidence=EVIDENCE,
        assessment_requests=[
            {
                "stack": "stacks/media/example",
                "question": "Assess migration",
                "evidence_ids": ["example-app-update"],
            }
        ],
        now=CREATED,
    )
    artifact = json.loads(destination.read_bytes())
    artifact["renovate_logs"] = "raw log"
    artifact["temporary_scan_directory"] = "/tmp/scan"

    with pytest.raises(ArtifactError, match="sensitive material"):
        validate_artifact(artifact)
