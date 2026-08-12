#!/usr/bin/env python3
"""Read-only anti-corruption boundary around one pinned Renovate lookup batch."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator


RENOVATE_VERSION = "44.5.0"
ADAPTER_VERSION = "1.0.0"
COMMAND = [
    "npx", "--yes", "--package", f"renovate@{RENOVATE_VERSION}", "renovate",
    "--platform=local", "--dry-run=lookup", "--onboarding=false", "--require-config=ignored",
]
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
OUTPUT_SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "image-update-renovate-adapter" / "candidate-observations.schema.json"


class ContractError(ValueError):
    """An input, process, or raw-output condition that invalidates the batch."""


def _object(value: Any, at: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{at} must be an object")
    return value


def _list(value: Any, at: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{at} must be an array")
    return value


def _string(value: Any, at: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{at} must be a non-empty string")
    return value


def _exact_keys(value: dict[str, Any], required: set[str], optional: set[str], at: str) -> None:
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        raise ContractError(f"{at} missing {sorted(missing)[0]}")
    if extra:
        raise ContractError(f"{at} has unexpected field {sorted(extra)[0]}")


def _split_reference(reference: str, at: str) -> tuple[str, str, str | None]:
    base, separator, digest = reference.partition("@")
    final = base.rsplit("/", 1)[-1]
    if ":" not in final:
        raise ContractError(f"{at} must contain an explicit tag")
    image, tag = base.rsplit(":", 1)
    if separator and not DIGEST.fullmatch(digest):
        raise ContractError(f"{at} has an invalid digest")
    return image, tag, digest if separator else None


def validate_request(value: Any) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    request = _object(value, "request schema root")
    _exact_keys(request, {"schema_version", "stacks"}, set(), "request schema root")
    if request["schema_version"] != 1:
        raise ContractError("request schema_version must be 1")
    stacks = _list(request["stacks"], "request schema stacks")
    if not stacks:
        raise ContractError("request schema stacks must not be empty")
    index: dict[str, dict[str, Any]] = {}
    stack_ids: set[tuple[str, str]] = set()
    for stack_number, raw_stack in enumerate(stacks):
        stack = _object(raw_stack, f"request schema stacks[{stack_number}]")
        _exact_keys(stack, {"host", "stack", "tracking_mode", "services"}, {"vendor"}, f"request schema stacks[{stack_number}]")
        host = _string(stack["host"], "request schema host")
        stack_name = _string(stack["stack"], "request schema stack")
        if not IDENTITY.fullmatch(host) or not IDENTITY.fullmatch(stack_name):
            raise ContractError("request schema host and stack must be path-safe identities")
        if (host, stack_name) in stack_ids:
            raise ContractError("request schema contains a duplicate stack identity")
        stack_ids.add((host, stack_name))
        if stack["tracking_mode"] not in ("image", "vendor"):
            raise ContractError("request schema tracking_mode must be image or vendor")
        if stack["tracking_mode"] == "vendor" and "vendor" not in stack:
            raise ContractError("request schema vendor tracking requires vendor provenance")
        if "vendor" in stack:
            _object(stack["vendor"], "request schema vendor")
        services = _list(stack["services"], "request schema services")
        if not services:
            raise ContractError("request schema services must not be empty")
        for service_number, raw_service in enumerate(services):
            service = _object(raw_service, f"request schema services[{service_number}]")
            required = {"service", "image", "current_effective_reference", "current_digest", "track"}
            _exact_keys(service, required, {"candidate_effective_reference"}, "request schema service")
            service_name = _string(service["service"], "request schema service identity")
            image = _string(service["image"], "request schema image")
            if not IDENTITY.fullmatch(service_name):
                raise ContractError("request schema service must be a path-safe identity")
            current_digest = _string(service["current_digest"], "request schema current_digest")
            if not DIGEST.fullmatch(current_digest):
                raise ContractError("request schema current_digest must be sha256")
            current_image, current_tag, embedded_digest = _split_reference(
                _string(service["current_effective_reference"], "request schema current_effective_reference"),
                "request schema current_effective_reference",
            )
            if current_image != image or embedded_digest not in (None, current_digest):
                raise ContractError("request schema current effective reference disagrees with image or digest")
            track = _object(service["track"], "request schema track")
            _exact_keys(track, {"kind", "value"}, set(), "request schema track")
            if track["kind"] not in ("floating-tag", "exact-version", "major-line"):
                raise ContractError("request schema has unsupported track kind")
            if (
                not isinstance(track["value"], (str, int))
                or isinstance(track["value"], bool)
                or track["value"] == ""
            ):
                raise ContractError("request schema track value must be a non-empty string or integer")
            if track["kind"] == "floating-tag" and str(track["value"]) != current_tag:
                raise ContractError("request schema floating track must equal the current tag")
            if "candidate_effective_reference" in service:
                candidate_image, candidate_tag, _ = _split_reference(service["candidate_effective_reference"], "request schema candidate_effective_reference")
                if candidate_image != image:
                    raise ContractError("request schema candidate effective reference disagrees with image")
                if track["kind"] == "exact-version" and candidate_tag != str(track["value"]):
                    raise ContractError("request schema exact track must equal the vendor candidate tag")
            relative = f"projections/{host}/{stack_name}/{service_name}/compose.yaml"
            if relative in index:
                raise ContractError("request schema contains a duplicate service identity")
            index[relative] = {"stack": stack, "service": service, "current_tag": current_tag}
    return request, index


def _config(index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rules: list[dict[str, Any]] = [{"matchDatasources": ["docker"], "pinDigests": True, "separateMajorMinor": True}]
    for path, item in index.items():
        track = item["service"]["track"]
        if track["kind"] == "floating-tag":
            rules.append({"matchFileNames": [path], "allowedVersions": f"/^{re.escape(str(track['value']))}$/"})
    return {"$schema": "https://docs.renovatebot.com/renovate-schema.json", "enabledManagers": ["docker-compose"], "packageRules": rules}


def _write_scan(root: Path, request: dict[str, Any], index: dict[str, dict[str, Any]]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative, item in index.items():
        service = item["service"]
        content = f"services:\n  {service['service']}:\n    image: {service['image']}:{item['current_tag']}@{service['current_digest']}\n"
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_text(content)
        hashes[relative] = hashlib.sha256(content.encode()).hexdigest()
    (root / "renovate.json").write_text(json.dumps(_config(index), indent=2) + "\n")
    return hashes


def _records(output: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _validate_normalized_output(value: dict[str, Any]) -> None:
    schema = json.loads(OUTPUT_SCHEMA.read_text())
    error = next(Draft202012Validator(schema).iter_errors(value), None)
    if error is not None:
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise ContractError(f"normalized schema is invalid at {location}")


def _validate_update(value: Any, at: str) -> dict[str, Any]:
    update = _object(value, at)
    for key in ("updateType", "newValue"):
        _string(update.get(key), f"{at}.{key}")
    if update["updateType"] not in {"digest", "patch", "minor", "major"}:
        raise ContractError(f"{at}.updateType is unsupported")
    if "newDigest" in update and (not isinstance(update["newDigest"], str) or not DIGEST.fullmatch(update["newDigest"])):
        raise ContractError(f"{at}.newDigest must be sha256")
    if "newMajor" in update and not isinstance(update["newMajor"], (str, int)):
        raise ContractError(f"{at}.newMajor must be a string or integer")
    return update


def _select(service: dict[str, Any], updates: list[dict[str, Any]]) -> dict[str, Any] | None:
    kind, value = service["track"]["kind"], str(service["track"]["value"])
    if kind == "floating-tag":
        return next((u for u in updates if u["updateType"] == "digest" and u["newValue"] == value), None)
    if kind == "exact-version":
        return next((u for u in updates if u["updateType"] != "digest" and u["newValue"] == value), None)
    return next((u for u in updates if u["updateType"] != "major" and str(u.get("newMajor")) == value), None)


def _normalize(request: dict[str, Any], index: dict[str, dict[str, Any]], batch: dict[str, Any], fingerprint: str) -> dict[str, Any]:
    if batch.get("level") != 20 or batch.get("msg") != "packageFiles with updates":
        raise ContractError("raw schema batch record identity is invalid")
    config = _object(batch.get("config"), "raw schema config")
    packages = _list(config.get("docker-compose"), "raw schema docker-compose")
    raw_by_path: dict[str, dict[str, Any]] = {}
    for package_number, raw_package in enumerate(packages):
        package = _object(raw_package, f"raw schema package[{package_number}]")
        path = _string(package.get("packageFile"), "raw schema packageFile")
        if path not in index:
            raise ContractError(f"unexpected projection {path}")
        if path in raw_by_path:
            raise ContractError(f"duplicate projection {path}")
        deps = _list(package.get("deps"), f"raw schema {path}.deps")
        if len(deps) != 1:
            raise ContractError(f"cardinality for {path} must be one dependency")
        dep = _object(deps[0], f"raw schema {path}.dependency")
        for key in ("depName", "currentValue", "currentDigest", "datasource"):
            _string(dep.get(key), f"raw schema {path}.{key}")
        updates = [_validate_update(item, f"raw schema {path}.updates") for item in _list(dep.get("updates"), f"raw schema {path}.updates")]
        warnings = _list(dep.get("warnings"), f"raw schema {path}.warnings")
        for warning in warnings:
            _string(_object(warning, f"raw schema {path}.warning").get("message"), f"raw schema {path}.warning.message")
        for link_name in ("sourceUrl", "homepage"):
            if link_name in dep:
                _string(dep[link_name], f"raw schema {path}.{link_name}")
        if dep["datasource"] != "docker":
            raise ContractError(f"dependency identity for {path} is not docker")
        expected = index[path]
        service = expected["service"]
        if (dep["depName"], dep["currentValue"], dep["currentDigest"]) != (service["image"], expected["current_tag"], service["current_digest"]):
            raise ContractError(f"dependency identity or current digest for {path} disagrees with request")
        raw_by_path[path] = {"dep": dep, "updates": updates, "warnings": warnings}
    missing = index.keys() - raw_by_path.keys()
    if missing:
        raise ContractError(f"missing projection {sorted(missing)[0]}")

    observations: list[dict[str, Any]] = []
    for path, expected in index.items():
        stack, service = expected["stack"], expected["service"]
        dep_data = raw_by_path[path]
        dep, updates, warnings = dep_data["dep"], dep_data["updates"], dep_data["warnings"]
        selected = None if warnings else _select(service, updates)
        candidate = None
        if selected:
            digest = selected.get("newDigest")
            if digest is None:
                raise ContractError(f"raw schema selected update for {path} has no comparable digest")
            supplied_reference = service.get("candidate_effective_reference")
            if supplied_reference is not None:
                _, _, supplied_digest = _split_reference(
                    supplied_reference,
                    "request schema candidate_effective_reference",
                )
                if supplied_digest is not None and supplied_digest != digest:
                    raise ContractError(
                        f"request schema candidate effective reference digest disagrees with Renovate selection for {path}"
                    )
            exact_ref = f"{service['image']}:{selected['newValue']}" + (f"@{digest}" if digest else "")
            candidate = {"effective_reference": supplied_reference or exact_ref, "digest": digest, "update_type": selected["updateType"], "proposed_exact_reference": exact_ref}
        alternatives = []
        for update in updates:
            if update["updateType"] == "major":
                digest = update.get("newDigest")
                if digest is None:
                    raise ContractError(f"raw schema visible major update for {path} has no comparable digest")
                alternatives.append({"effective_reference": f"{service['image']}:{update['newValue']}@{digest}", "version": update["newValue"], "digest": digest})
        observations.append({
            "identity": {"host": stack["host"], "stack": stack["stack"], "service": service["service"]},
            "tracking_mode": stack["tracking_mode"],
            "status": "lookup-failed" if warnings else ("candidate" if selected else "no-update"),
            "current": {"effective_reference": service["current_effective_reference"], "digest": dep["currentDigest"]},
            "candidate": candidate,
            "visible_major_alternatives": alternatives,
            "upstream_links": {"source": dep.get("sourceUrl"), "homepage": dep.get("homepage")},
            "limitations": [{"kind": "dependency-lookup-warning", "message": item["message"]} for item in warnings],
            "vendor": stack.get("vendor"),
        })
    stacks = []
    for stack in request["stacks"]:
        members = [o for o in observations if (o["identity"]["host"], o["identity"]["stack"]) == (stack["host"], stack["stack"])]
        stacks.append({"host": stack["host"], "stack": stack["stack"], "scan_status": "incomplete" if any(o["status"] == "lookup-failed" for o in members) else "complete", "observation_count": len(members)})
    return {
        "schema_version": 1,
        "provenance": {"adapter_version": ADAPTER_VERSION, "renovate_version": RENOVATE_VERSION, "request_fingerprint": fingerprint, "scan_time": datetime.now(timezone.utc).isoformat(), "scope": [{"host": s["host"], "stack": s["stack"]} for s in request["stacks"]]},
        "stacks": stacks,
        "observations": observations,
    }


def scan(request_value: Any) -> dict[str, Any]:
    request, index = validate_request(request_value)
    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="image-update-renovate-") as temporary:
        root = Path(temporary)
        before = _write_scan(root, request, index)
        environment = {key: value for key, value in os.environ.items() if not key.startswith("RENOVATE_")}
        environment.update({"RENOVATE_CONFIG_FILE": "renovate.json", "LOG_LEVEL": "debug", "LOG_FORMAT": "json"})
        completed = subprocess.run(COMMAND, cwd=root, env=environment, text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            raise ContractError(f"Renovate process exited {completed.returncode}")
        records = _records(completed.stdout + "\n" + completed.stderr)
        starts = [record for record in records if record.get("msg") == "Renovate started"]
        batches = [record for record in records if record.get("msg") == "packageFiles with updates"]
        if len(starts) != 1 or len(batches) != 1:
            raise ContractError(f"raw record cardinality expected one start and one batch, got {len(starts)} and {len(batches)}")
        start = starts[0]
        if (
            start.get("level") != 30
            or start.get("msg") != "Renovate started"
            or start.get("renovateVersion") != RENOVATE_VERSION
        ):
            raise ContractError(
                f"Renovate started record must have level 30 and exact version {RENOVATE_VERSION}"
            )
        after = {relative: hashlib.sha256((root / relative).read_bytes()).hexdigest() if (root / relative).is_file() else "missing" for relative in before}
        actual = {path.relative_to(root).as_posix() for path in root.glob("projections/**/compose.yaml")}
        if before != after or actual != set(before):
            raise ContractError("projection mutation detected")
        result = _normalize(request, index, batches[0], fingerprint)
        _validate_normalized_output(result)
        return result


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} REQUEST.json", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(sys.argv[1]).read_text())
        result = scan(request)
    except (ContractError, OSError, json.JSONDecodeError) as error:
        print(f"batch invalid: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
