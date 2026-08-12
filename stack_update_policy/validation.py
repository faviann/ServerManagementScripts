from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml


PORTABILITY_TIERS = {"portable-app", "host-bound-app", "foundational-controlled-migration"}
TRAEFIK_EXPOSURES = {"none", "protected", "public"}
SECRET_KEY = re.compile(
    r"(^|_)(?:api_?key|access_?key|credential(?:s)?|password(?:s)?|passwd|"
    r"private_?key|secret(?:s)?|token(?:s)?)(_|$)"
)
SECRET_VALUE = re.compile(
    r"(\$ANSIBLE_VAULT|!vault|BEGIN (?:(?:(?:RSA|OPENSSH|EC|DSA|ENCRYPTED) )?PRIVATE KEY|PGP PRIVATE KEY BLOCK)|"
    r"\bvault[-_:/.][A-Za-z0-9_.:/-]+|://[^\s/:]+:[^\s/@]+@|"
    r"\b(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+|"
    r"\b(?:gh[opusr]|github_pat)_[A-Za-z0-9_]{16,}|\bAKIA[A-Z0-9]{16}\b|"
    r"\bBearer\s+[A-Za-z0-9._~-]{12,}|\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ValidationError:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass(frozen=True)
class ValidatedStackPolicy:
    identity: str
    host: str
    name: str
    metadata: dict[str, Any]
    policy: dict[str, Any]
    services: dict[str, dict[str, str]]
    procedure: dict[str, str] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "identity": self.identity,
            "metadata": self.metadata,
            "name": self.name,
            "policy": self.policy,
            "procedure": self.procedure,
            "services": self.services,
        }


@dataclass(frozen=True)
class StackPolicyValidation:
    errors: tuple[ValidationError, ...]
    result: ValidatedStackPolicy | None

    def __post_init__(self) -> None:
        if not self.errors and self.result is None:
            raise ValueError("successful validation requires a result")
        if self.errors and self.result is not None:
            raise ValueError("failed validation cannot include a result")

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": "validate",
            "errors": [error.as_dict() for error in self.errors],
            "result": self.result.as_dict() if self.result else None,
            "schema_version": 1,
            "valid": self.valid,
        }


def _error(errors: list[ValidationError], code: str, path: str, message: str) -> None:
    errors.append(ValidationError(code, path, message))


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_secret_key(key: str) -> bool:
    key_with_word_boundaries = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", key)
    key_with_word_boundaries = re.sub(
        r"([a-z0-9])([A-Z])", r"\1_\2", key_with_word_boundaries
    )
    normalized_key = re.sub(
        r"[^a-z0-9]+", "_", key_with_word_boundaries.lower()
    ).strip("_")
    return bool(SECRET_KEY.search(normalized_key) or "vault" in normalized_key)


def _mapping_child_path(
    path: str, key: Any, secret_key_number: int | None = None
) -> str:
    if not isinstance(key, str):
        return f"{path}[<non-string-key>]"
    if _is_secret_key(key):
        suffix = f"-{secret_key_number}" if secret_key_number is not None else ""
        return f"{path}.<secret-key{suffix}>"
    return f"{path}.{key}"


def _markdown_targets(document: str) -> set[str]:
    targets: set[str] = set()
    heading_counts: dict[str, int] = {}
    fence: tuple[str, int] | None = None
    in_comment = False
    previous_line: str | None = None

    def heading_slug(title: str) -> str:
        title = re.sub(r"<[^>]+>", "", title).strip().lower()
        slug = re.sub(r"[^\w\- ]", "", title, flags=re.UNICODE)
        return re.sub(r"\s+", "-", slug)

    def add_heading(title: str) -> None:
        slug = heading_slug(title)
        duplicate_number = heading_counts.get(slug, 0)
        targets.add(f"{slug}-{duplicate_number}" if duplicate_number else slug)
        heading_counts[slug] = duplicate_number + 1

    for line in document.splitlines():
        if fence is not None:
            closing_fence = re.match(r"^ {0,3}([`~]+)\s*$", line)
            if (
                closing_fence
                and closing_fence.group(1)[0] == fence[0]
                and len(closing_fence.group(1)) >= fence[1]
            ):
                fence = None
            continue
        visible: list[str] = []
        cursor = 0
        while cursor < len(line):
            if in_comment:
                comment_end = line.find("-->", cursor)
                if comment_end == -1:
                    cursor = len(line)
                else:
                    in_comment = False
                    cursor = comment_end + 3
                continue
            comment_start = line.find("<!--", cursor)
            if comment_start == -1:
                visible.append(line[cursor:])
                break
            visible.append(line[cursor:comment_start])
            in_comment = True
            cursor = comment_start + 4
        line = "".join(visible)
        opening_fence = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if opening_fence:
            marker = opening_fence.group(1)
            fence = (marker[0], len(marker))
            previous_line = None
            continue
        setext = re.match(r"^ {0,3}(?:=+|-+)\s*$", line)
        if setext and previous_line is not None:
            add_heading(previous_line)
            previous_line = None
            continue
        heading = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            add_heading(heading.group(1))
            previous_line = None
        for anchor in re.finditer(
            r"<a\s+[^>]*(?:id|name)\s*=\s*(['\"])([^'\"]+)\1[^>]*>",
            line,
            flags=re.IGNORECASE,
        ):
            targets.add(anchor.group(2))
        if not heading:
            previous_line = line.strip() or None
    return targets


class _RecursiveYamlAliasError(Exception):
    def __init__(self, path: str) -> None:
        self.path = path


def _validate_json_metadata(
    value: Any,
    errors: list[ValidationError],
    path: str = "stack.yaml",
    active_containers: set[int] | None = None,
) -> None:
    if active_containers is None:
        active_containers = set()
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in active_containers:
            raise _RecursiveYamlAliasError(path)
        active_containers.add(identity)
    if isinstance(value, dict):
        try:
            secret_key_number = 0
            for key, child in value.items():
                if not isinstance(key, str):
                    _error(
                        errors,
                        "invalid-metadata",
                        path,
                        "metadata mapping keys must be strings",
                    )
                    continue
                if _is_secret_key(key):
                    secret_key_number += 1
                _validate_json_metadata(
                    child,
                    errors,
                    _mapping_child_path(path, key, secret_key_number),
                    active_containers,
                )
        finally:
            active_containers.remove(id(value))
    elif isinstance(value, list):
        try:
            for index, child in enumerate(value):
                _validate_json_metadata(
                    child,
                    errors,
                    f"{path}[{index}]",
                    active_containers,
                )
        finally:
            active_containers.remove(id(value))
    elif type(value) is float:
        if not math.isfinite(value):
            _error(
                errors,
                "invalid-metadata",
                path,
                "metadata value must be JSON-compatible",
            )
    elif value is not None and type(value) not in {bool, int, str}:
        _error(
            errors,
            "invalid-metadata",
            path,
            "metadata value must be JSON-compatible",
        )


def _find_secrets(
    value: Any, path: str = "stack.yaml", active_containers: set[int] | None = None
) -> list[str]:
    found: list[str] = []
    if active_containers is None:
        active_containers = set()
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in active_containers:
            raise _RecursiveYamlAliasError(path)
        active_containers.add(identity)
    if isinstance(value, dict):
        try:
            secret_key_number = 0
            for key, child in value.items():
                if isinstance(key, str) and _is_secret_key(key):
                    secret_key_number += 1
                child_path = _mapping_child_path(path, key, secret_key_number)
                if not isinstance(key, str):
                    found.extend(_find_secrets(child, child_path, active_containers))
                    continue
                if _is_secret_key(key):
                    found.append(child_path)
                found.extend(_find_secrets(child, child_path, active_containers))
        finally:
            active_containers.remove(id(value))
    elif isinstance(value, list):
        try:
            for index, child in enumerate(value):
                found.extend(
                    _find_secrets(child, f"{path}[{index}]", active_containers)
                )
        finally:
            active_containers.remove(id(value))
    elif isinstance(value, str):
        lowered = value.lower()
        if SECRET_VALUE.search(value) or "{{ vault_" in lowered or "{{vault_" in lowered:
            found.append(path)
    return found


def _load_metadata(stack_root: Path, errors: list[ValidationError]) -> dict[str, Any] | None:
    manifest = stack_root / "stack.yaml"
    if not manifest.is_file():
        _error(errors, "missing-metadata", "stack.yaml", "stack.yaml is required")
        return None
    try:
        loaded = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        if isinstance(getattr(mark, "line", None), int) and isinstance(
            getattr(mark, "column", None), int
        ):
            message = (
                f"could not parse stack.yaml at line {mark.line + 1}, "
                f"column {mark.column + 1}"
            )
        else:
            message = "could not parse stack.yaml"
        _error(errors, "malformed-metadata", "stack.yaml", message)
        return None
    except (OSError, UnicodeError):
        _error(errors, "malformed-metadata", "stack.yaml", "could not read stack.yaml")
        return None
    if not isinstance(loaded, dict):
        _error(errors, "malformed-metadata", "stack.yaml", "stack.yaml must contain a mapping")
        return None
    try:
        _validate_json_metadata(loaded, errors)
    except _RecursiveYamlAliasError as exc:
        _error(
            errors,
            "malformed-metadata",
            exc.path,
            "recursive YAML aliases are not supported",
        )
        return None
    try:
        secret_paths = _find_secrets(loaded)
    except _RecursiveYamlAliasError as exc:
        _error(
            errors,
            "malformed-metadata",
            exc.path,
            "recursive YAML aliases are not supported",
        )
        return None
    for secret_path in sorted(set(secret_paths)):
        _error(errors, "secret-metadata", secret_path, "secret-shaped metadata is forbidden")
    return loaded


def _validate_minimum_metadata(metadata: dict[str, Any], folder_name: str, errors: list[ValidationError]) -> None:
    required = {
        "schema_version": int,
        "kind": str,
        "name": str,
        "description": str,
        "portability": dict,
        "exposure": dict,
    }
    for key, expected_type in required.items():
        value = metadata.get(key)
        has_expected_type = (
            type(value) is int
            if expected_type is int
            else isinstance(value, expected_type)
        )
        if not has_expected_type or expected_type is str and not value.strip():
            _error(errors, "invalid-metadata", f"stack.yaml.{key}", f"{key} is required and must be {expected_type.__name__}")
    if type(metadata.get("schema_version")) is not int or metadata.get("schema_version") != 1:
        _error(errors, "invalid-value", "stack.yaml.schema_version", "only schema_version 1 is supported")
    if metadata.get("kind") != "stack":
        _error(errors, "invalid-value", "stack.yaml.kind", "kind must be stack")
    if isinstance(metadata.get("name"), str) and metadata["name"] != folder_name:
        _error(errors, "folder-name-mismatch", "stack.yaml.name", "name must match the stack folder")

    portability = metadata.get("portability")
    if isinstance(portability, dict):
        if portability.get("tier") not in PORTABILITY_TIERS:
            _error(errors, "invalid-value", "stack.yaml.portability.tier", "unsupported portability tier")
        if portability.get("owner") != "stack":
            _error(errors, "invalid-value", "stack.yaml.portability.owner", "owner must be stack")

    exposure = metadata.get("exposure")
    if isinstance(exposure, dict):
        if exposure.get("traefik") not in TRAEFIK_EXPOSURES:
            _error(errors, "invalid-value", "stack.yaml.exposure.traefik", "unsupported Traefik exposure")
        homepage = exposure.get("homepage_instances")
        if not isinstance(homepage, list) or not all(_is_nonempty_string(item) for item in homepage):
            _error(errors, "invalid-metadata", "stack.yaml.exposure.homepage_instances", "homepage_instances must be a list of names")


def _resolve_compose(stack_root: Path, errors: list[ValidationError]) -> dict[str, Any] | None:
    base_files = [path for path in (stack_root / "compose.yaml", stack_root / "compose.yml") if path.is_file()]
    override_files = [path for path in (stack_root / "compose.override.yaml", stack_root / "compose.override.yml") if path.is_file()]
    if len(base_files) != 1:
        _error(errors, "compose-resolution", "compose", "exactly one compose.yaml or compose.yml is required")
        return None
    if len(override_files) > 1:
        _error(errors, "compose-resolution", "compose", "at most one supported Compose override is allowed")
        return None
    command = ["docker", "compose"]
    for path in base_files + override_files:
        command.extend(("-f", str(path)))
    command.extend(("config", "--format", "json", "--no-interpolate", "--no-env-resolution"))
    try:
        completed = subprocess.run(command, cwd=stack_root, text=True, capture_output=True, check=False)
    except OSError as exc:
        _error(errors, "compose-resolution", "compose", f"could not run Docker Compose: {exc}")
        return None
    if completed.returncode != 0:
        _error(
            errors,
            "compose-resolution",
            "compose",
            "Docker Compose could not resolve the stack definition",
        )
        return None
    try:
        effective = json.loads(completed.stdout)
    except json.JSONDecodeError:
        _error(
            errors,
            "compose-resolution",
            "compose",
            "Docker Compose returned an invalid stack definition",
        )
        return None
    if not isinstance(effective, dict):
        _error(
            errors,
            "compose-resolution",
            "compose",
            "Docker Compose returned an invalid stack definition",
        )
        return None
    if not isinstance(effective.get("services"), dict):
        _error(
            errors,
            "compose-resolution",
            "compose",
            "Docker Compose returned an invalid stack definition",
        )
        return None
    for service in effective["services"].values():
        if not isinstance(service, dict) or (
            "image" in service and not _is_nonempty_string(service["image"])
        ):
            _error(
                errors,
                "compose-resolution",
                "compose",
                "Docker Compose returned an invalid stack definition",
            )
            return None
    return effective


def _validate_procedure(
    stack_root: Path,
    updates: dict[str, Any],
    errors: list[ValidationError],
) -> dict[str, str] | None:
    procedure = updates.get("procedure")
    if procedure is None:
        return None
    if not isinstance(procedure, dict) or set(procedure) != {"mode", "runbook"}:
        _error(errors, "invalid-procedure", "stack.yaml.updates.procedure", "procedure must contain only mode and runbook")
        return None
    if procedure.get("mode") != "assisted":
        _error(errors, "invalid-procedure", "stack.yaml.updates.procedure.mode", "only assisted procedures are supported")
    runbook = procedure.get("runbook")
    if not _is_nonempty_string(runbook):
        _error(errors, "invalid-runbook", "stack.yaml.updates.procedure.runbook", "runbook must be a local path")
        return None
    runbook_path, fragment_marker, fragment = runbook.partition("#")
    if fragment_marker and not fragment:
        _error(errors, "invalid-runbook", "stack.yaml.updates.procedure.runbook", "runbook fragment must not be empty")
        return None
    if fragment_marker and (
        any(character.isspace() or character == "#" for character in fragment)
        or re.search(r"%(?![0-9A-Fa-f]{2})", fragment)
    ):
        _error(errors, "invalid-runbook", "stack.yaml.updates.procedure.runbook", "runbook fragment is invalid")
        return None
    relative_runbook = Path(runbook_path)
    if relative_runbook.is_absolute() or ".." in relative_runbook.parts:
        _error(
            errors,
            "invalid-runbook",
            "stack.yaml.updates.procedure.runbook",
            "runbook must stay within the selected stack",
        )
        return None
    try:
        candidate = (stack_root / relative_runbook).resolve(strict=True)
    except FileNotFoundError:
        _error(
            errors,
            "invalid-runbook",
            "stack.yaml.updates.procedure.runbook",
            "runbook does not resolve to a local file",
        )
        return None
    except (OSError, RuntimeError, ValueError):
        _error(
            errors,
            "invalid-runbook",
            "stack.yaml.updates.procedure.runbook",
            "runbook path could not be resolved",
        )
        return None
    try:
        candidate.relative_to(stack_root)
    except ValueError:
        _error(
            errors,
            "invalid-runbook",
            "stack.yaml.updates.procedure.runbook",
            "runbook must stay within the selected stack",
        )
        return None
    if not candidate.is_file():
        _error(errors, "invalid-runbook", "stack.yaml.updates.procedure.runbook", "runbook does not resolve to a local file")
        return None
    if fragment_marker:
        try:
            document = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            _error(errors, "invalid-runbook", "stack.yaml.updates.procedure.runbook", "runbook is not readable Markdown")
            return None
        if unquote(fragment) not in _markdown_targets(document):
            _error(
                errors,
                "invalid-runbook",
                "stack.yaml.updates.procedure.runbook",
                "runbook fragment does not resolve to a Markdown target",
            )
            return None
    return {"mode": "assisted", "runbook": runbook}


def _validate_image_policy(
    stack_root: Path,
    metadata: dict[str, Any],
    effective: dict[str, Any] | None,
    errors: list[ValidationError],
) -> tuple[dict[str, dict[str, str]], dict[str, str] | None]:
    updates = metadata.get("updates")
    if not isinstance(updates, dict):
        _error(errors, "missing-policy", "stack.yaml.updates", "updates policy is required")
        return {}, None
    if updates.get("mode") != "images":
        _error(errors, "unsupported-mode", "stack.yaml.updates.mode", "strict validation supports only images mode")
    allowed_update_keys = {"mode", "track", "services", "procedure", "low_confidence"}
    unknown = sorted(
        (key for key in updates if not isinstance(key, str) or key not in allowed_update_keys),
        key=str,
    )
    if unknown:
        rendered_unknown = ", ".join(
            (
                "<secret-key>"
                if isinstance(key, str) and _is_secret_key(key)
                else key
                if isinstance(key, str)
                else "<non-string-key>"
            )
            for key in unknown
        )
        _error(
            errors,
            "invalid-policy",
            "stack.yaml.updates",
            f"unsupported policy fields: {rendered_unknown}",
        )
    if "low_confidence" in updates and updates["low_confidence"] != "assisted":
        _error(
            errors,
            "invalid-value",
            "stack.yaml.updates.low_confidence",
            "low_confidence must be assisted",
        )
    default_track = updates.get("track")
    if default_track is not None and not _is_nonempty_string(default_track):
        _error(errors, "invalid-value", "stack.yaml.updates.track", "image update track must be a non-empty string")
        default_track = None
    service_policies = updates.get("services", {})
    if not isinstance(service_policies, dict):
        _error(errors, "invalid-policy", "stack.yaml.updates.services", "services must be a mapping")
        service_policies = {}

    procedure = _validate_procedure(stack_root, updates, errors)
    if effective is None:
        return {}, procedure
    compose_services = effective["services"]
    for service_name, policy in sorted(service_policies.items(), key=lambda item: str(item[0])):
        path = _mapping_child_path("stack.yaml.updates.services", service_name)
        if service_name not in compose_services:
            _error(errors, "unknown-service", path, "policy service is not in effective Compose")
            continue
        if "image" not in compose_services[service_name]:
            _error(errors, "non-image-service", path, "tracked service has no effective image")
        if not isinstance(policy, dict) or set(policy) != {"track"}:
            _error(errors, "invalid-policy", path, "service policy must contain only track")
            continue
        if not _is_nonempty_string(policy.get("track")):
            _error(errors, "invalid-value", f"{path}.track", "image update track must be a non-empty string")

    resolved: dict[str, dict[str, str]] = {}
    for service_name, service in sorted(compose_services.items()):
        if "image" not in service:
            continue
        policy = service_policies.get(service_name)
        service_track = policy.get("track") if isinstance(policy, dict) else None
        track = service_track or default_track
        if not _is_nonempty_string(track):
            _error(
                errors,
                "missing-track",
                _mapping_child_path("stack.yaml.updates.services", service_name),
                "image-bearing service has no effective image update track",
            )
            continue
        resolved[service_name] = {"image": str(service["image"]), "track": track}
    return resolved, procedure


def validate_stack(repository_root: Path, identity: str) -> StackPolicyValidation:
    errors: list[ValidationError] = []
    parts = Path(identity).parts
    if len(parts) != 3 or parts[0] != "stacks" or any(part in {"", ".", ".."} for part in parts):
        error = ValidationError("invalid-identity", "identity", "expected stacks/<host>/<stack>")
        return StackPolicyValidation((error,), None)
    host, name = parts[1:]
    try:
        resolved_repository_root = repository_root.resolve(strict=True)
    except FileNotFoundError:
        error = ValidationError(
            "missing-stack", "identity", "selected stack directory does not exist"
        )
        return StackPolicyValidation((error,), None)
    except (OSError, RuntimeError, ValueError):
        error = ValidationError(
            "invalid-repository-root",
            "repository-root",
            "repository root could not be resolved",
        )
        return StackPolicyValidation((error,), None)
    try:
        stack_root = (resolved_repository_root / identity).resolve(strict=True)
    except FileNotFoundError:
        error = ValidationError(
            "missing-stack", "identity", "selected stack directory does not exist"
        )
        return StackPolicyValidation((error,), None)
    except (OSError, RuntimeError, ValueError):
        error = ValidationError(
            "invalid-identity",
            "identity",
            "selected stack directory could not be resolved",
        )
        return StackPolicyValidation((error,), None)
    try:
        stack_root.relative_to(resolved_repository_root)
    except ValueError:
        error = ValidationError(
            "invalid-identity",
            "identity",
            "selected stack directory must stay within the repository",
        )
        return StackPolicyValidation((error,), None)
    if not stack_root.is_dir():
        error = ValidationError("missing-stack", "identity", "selected stack directory does not exist")
        return StackPolicyValidation((error,), None)

    metadata = _load_metadata(stack_root, errors)
    effective = _resolve_compose(stack_root, errors)
    services: dict[str, dict[str, str]] = {}
    procedure: dict[str, str] | None = None
    if metadata is not None:
        _validate_minimum_metadata(metadata, name, errors)
        services, procedure = _validate_image_policy(stack_root, metadata, effective, errors)

    ordered_errors = tuple(sorted(errors, key=lambda error: (error.path, error.code, error.message)))
    if ordered_errors:
        return StackPolicyValidation(ordered_errors, None)
    normalized_metadata = {
        key: metadata[key]
        for key in ("schema_version", "kind", "name", "description", "portability", "exposure")
    }
    updates = metadata["updates"]
    normalized_policy = {
        "low_confidence": updates.get("low_confidence"),
        "mode": "images",
        "track": updates.get("track"),
    }
    result = ValidatedStackPolicy(
        identity,
        host,
        name,
        normalized_metadata,
        normalized_policy,
        services,
        procedure,
    )
    return StackPolicyValidation((), result)
