"""Durable review artifacts for resumable image-update planning."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


_SCHEMA_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "schemas/image-update-artifacts"
)
_LIFETIME = timedelta(hours=24)
_SENSITIVE_MATERIAL = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}\b"
    r"|\bAuthorization\s*:\s*Bearer\s+\S+"
    r"|\bAWS_(?:ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)\s*=\s*\S+"
    r"|(?<![A-Za-z0-9._/-])/tmp/[^\s\"'<>]+"
    r'|\{(?=[^\r\n]*"level"\s*:\s*\d+)(?=[^\r\n]*"msg"\s*:)'
    r"|\b(?:api[_ -]?key|password|secret|token)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


class ArtifactError(ValueError):
    """An artifact is not safe to resume or publish."""


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ArtifactError("artifact clock must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _schema(kind: str, version: object) -> dict[str, Any]:
    if type(version) is not int or version != 1:
        raise ArtifactError("unsupported artifact schema version")
    path = _SCHEMA_DIRECTORY / f"{kind}-v{version}.schema.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError("unsupported artifact kind") from exc


def validate_artifact(artifact: object) -> None:
    """Validate a draft or final artifact against its versioned schema."""
    if not isinstance(artifact, dict):
        raise ArtifactError("artifact must be a JSON object")
    if _contains_sensitive_material(artifact):
        raise ArtifactError("artifact contains sensitive material")
    kind = artifact.get("artifact_kind")
    if kind not in {"draft", "final"}:
        raise ArtifactError("unsupported artifact kind")
    validator = Draft202012Validator(
        _schema(kind, artifact.get("schema_version")),
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(artifact), key=lambda error: list(error.path))
    if errors:
        raise ArtifactError(f"artifact does not match schema: {errors[0].message}")
    created = _parse_timestamp(artifact["created_at"])
    expires = _parse_timestamp(artifact["expires_at"])
    if expires - created != _LIFETIME:
        raise ArtifactError("artifact expiry must be exactly 24 hours after creation")
    evidence_ids = {item["id"] for item in artifact["evidence"]}
    if len(evidence_ids) != len(artifact["evidence"]):
        raise ArtifactError("artifact evidence IDs must be unique")
    fingerprinted_stacks = set(artifact["input_fingerprints"])
    if any(
        item["stack"] not in fingerprinted_stacks for item in artifact["evidence"]
    ):
        raise ArtifactError("artifact evidence stack is not fingerprint-bound")
    if kind == "draft":
        if any(
            request["stack"] not in fingerprinted_stacks
            for request in artifact["assessment_requests"]
        ):
            raise ArtifactError("artifact assessment stack is not fingerprint-bound")
        requested = {
            evidence_id
            for request in artifact["assessment_requests"]
            for evidence_id in request["evidence_ids"]
        }
        if not requested <= evidence_ids:
            raise ArtifactError("assessment request refers to unknown evidence")
        evidence_stacks = {
            item["id"]: item["stack"] for item in artifact["evidence"]
        }
        if any(
            evidence_stacks[evidence_id] != request["stack"]
            for request in artifact["assessment_requests"]
            for evidence_id in request["evidence_ids"]
        ):
            raise ArtifactError("artifact assessment stack does not match its evidence")


def _contains_sensitive_material(value: object) -> bool:
    if isinstance(value, str):
        return _SENSITIVE_MATERIAL.search(value) is not None
    if isinstance(value, dict):
        return any(
            _contains_sensitive_material(key) or _contains_sensitive_material(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_material(item) for item in value)
    return False


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArtifactError("artifact timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ArtifactError("artifact timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _write(path: Path, artifact: dict[str, object]) -> str:
    validate_artifact(artifact)
    encoded = json.dumps(
        artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return hashlib.sha256(encoded).hexdigest()


def write_draft_artifact(
    path: Path,
    *,
    repository: str,
    source_commit: str,
    input_fingerprints: Mapping[str, str],
    evidence: Sequence[Mapping[str, object]],
    assessment_requests: Sequence[Mapping[str, object]],
    now: datetime,
) -> str:
    """Atomically write a draft containing only pending assessment requests."""
    created = now.astimezone(UTC) if now.tzinfo is not None else now
    artifact: dict[str, object] = {
        "artifact_kind": "draft",
        "assessment_requests": [dict(request) for request in assessment_requests],
        "created_at": _timestamp(created),
        "evidence": [dict(item) for item in evidence],
        "expires_at": _timestamp(created + _LIFETIME),
        "input_fingerprints": dict(input_fingerprints),
        "repository": repository,
        "schema_version": 1,
        "source_commit": source_commit,
    }
    return _write(path, artifact)


def write_final_artifact(
    path: Path,
    *,
    repository: str,
    source_commit: str,
    input_fingerprints: Mapping[str, str],
    evidence: Sequence[Mapping[str, object]],
    publish_actions: Sequence[Mapping[str, object]],
    now: datetime,
) -> str:
    """Atomically write a final containing exact publishable GitHub actions."""
    created = now.astimezone(UTC) if now.tzinfo is not None else now
    artifact: dict[str, object] = {
        "artifact_kind": "final",
        "created_at": _timestamp(created),
        "evidence": [dict(item) for item in evidence],
        "expires_at": _timestamp(created + _LIFETIME),
        "input_fingerprints": dict(input_fingerprints),
        "publish_actions": [dict(action) for action in publish_actions],
        "repository": repository,
        "schema_version": 1,
        "source_commit": source_commit,
    }
    return _write(path, artifact)


def load_artifact(
    path: Path,
    *,
    expected_checksum: str,
    expected_kind: str,
    repository: str,
    source_commit: str,
    input_fingerprints: Mapping[str, str],
    now: datetime,
) -> dict[str, object]:
    """Load a context-bound artifact after matching its exact-byte checksum."""
    try:
        encoded = path.read_bytes()
    except OSError as exc:
        raise ArtifactError("artifact could not be read") from exc
    actual_checksum = hashlib.sha256(encoded).hexdigest()
    if not hmac.compare_digest(actual_checksum, expected_checksum):
        raise ArtifactError("artifact checksum does not match expected checksum")
    try:
        artifact = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactError("artifact is not valid JSON") from exc
    validate_artifact(artifact)
    assert isinstance(artifact, dict)
    if artifact["artifact_kind"] != expected_kind:
        raise ArtifactError("artifact kind does not match expected lifecycle state")
    if artifact["repository"] != repository:
        raise ArtifactError("artifact repository does not match")
    if artifact["source_commit"] != source_commit:
        raise ArtifactError("artifact source commit does not match")
    if artifact["input_fingerprints"] != dict(input_fingerprints):
        raise ArtifactError("artifact relevant inputs do not match")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ArtifactError("artifact clock must be timezone-aware")
    boundary = now.astimezone(UTC)
    if _parse_timestamp(artifact["expires_at"]) <= boundary:
        raise ArtifactError("artifact has expired")
    return artifact
