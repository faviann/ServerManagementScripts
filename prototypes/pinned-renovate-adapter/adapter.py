"""Pure validation and normalization logic for the throwaway adapter prototype."""

from __future__ import annotations

from typing import Any


PINNED_RENOVATE_VERSION = "44.5.0"
ADAPTER_VERSION = "prototype-1"


class ContractError(ValueError):
    pass


def _require(value: Any, expected: type, location: str) -> Any:
    if not isinstance(value, expected):
        raise ContractError(f"{location} must be {expected.__name__}")
    return value


def _service_index(request: dict[str, Any]) -> dict[str, dict[str, Any]]:
    _require(request.get("stacks"), list, "request.stacks")
    index: dict[str, dict[str, Any]] = {}
    for stack in request["stacks"]:
        _require(stack, dict, "request.stack")
        for key in ("host", "stack", "tracking_mode", "services"):
            if key not in stack:
                raise ContractError(f"request stack is missing {key}")
        for service in _require(stack["services"], list, "stack.services"):
            _require(service, dict, "stack.service")
            for key in ("service", "image", "current_effective_reference", "current_digest", "track"):
                if key not in service:
                    raise ContractError(f"request service is missing {key}")
            path = f"projections/{stack['host']}/{stack['stack']}/{service['service']}/compose.yaml"
            if path in index:
                raise ContractError(f"duplicate projection identity: {path}")
            index[path] = {"stack": stack, "service": service}
    return index


def _validate_dep(dep: Any, package_file: str) -> dict[str, Any]:
    dep = _require(dep, dict, f"{package_file}.dependency")
    for key, expected in {
        "depName": str,
        "currentValue": str,
        "currentDigest": str,
        "datasource": str,
        "updates": list,
        "warnings": list,
    }.items():
        _require(dep.get(key), expected, f"{package_file}.{key}")
    if dep["datasource"] != "docker":
        raise ContractError(f"{package_file}.datasource must be docker")
    for position, update in enumerate(dep["updates"]):
        update = _require(update, dict, f"{package_file}.updates[{position}]")
        _require(update.get("updateType"), str, f"{package_file}.updates[{position}].updateType")
        _require(update.get("newValue"), str, f"{package_file}.updates[{position}].newValue")
        if "newDigest" in update:
            _require(update["newDigest"], str, f"{package_file}.updates[{position}].newDigest")
    for position, warning in enumerate(dep["warnings"]):
        warning = _require(warning, dict, f"{package_file}.warnings[{position}]")
        _require(warning.get("message"), str, f"{package_file}.warnings[{position}].message")
    return dep


def _select_update(service: dict[str, Any], updates: list[dict[str, Any]]) -> dict[str, Any] | None:
    track = _require(service["track"], dict, "service.track")
    kind = track.get("kind")
    value = track.get("value")
    if kind == "floating-tag":
        return next((item for item in updates if item["updateType"] == "digest" and item["newValue"] == value), None)
    if kind == "exact-version":
        return next((item for item in updates if item["newValue"] == value and item["updateType"] != "digest"), None)
    if kind == "major-line":
        return next((item for item in updates if item.get("newMajor") == value and item["updateType"] != "major"), None)
    raise ContractError(f"unsupported track kind: {kind}")


def normalize(
    request: dict[str, Any],
    raw_record: dict[str, Any],
    renovate_version: str,
    request_fingerprint: str,
    scan_time: str,
) -> dict[str, Any]:
    if renovate_version != PINNED_RENOVATE_VERSION:
        raise ContractError(f"expected Renovate {PINNED_RENOVATE_VERSION}, got {renovate_version}")
    if raw_record.get("level") != 20 or raw_record.get("msg") != "packageFiles with updates":
        raise ContractError("unexpected Renovate batch record")
    raw_config = _require(raw_record.get("config"), dict, "raw.config")
    package_files = _require(raw_config.get("docker-compose"), list, "raw.config.docker-compose")
    expected = _service_index(request)
    seen: set[str] = set()
    observations: list[dict[str, Any]] = []

    for package in package_files:
        package = _require(package, dict, "raw.packageFile")
        package_file = _require(package.get("packageFile"), str, "raw.packageFile.packageFile")
        if package_file not in expected:
            raise ContractError(f"unexpected projection in Renovate output: {package_file}")
        if package_file in seen:
            raise ContractError(f"duplicate projection in Renovate output: {package_file}")
        seen.add(package_file)
        deps = _require(package.get("deps"), list, f"{package_file}.deps")
        if len(deps) != 1:
            raise ContractError(f"{package_file} must contain exactly one dependency")
        dep = _validate_dep(deps[0], package_file)
        item = expected[package_file]
        stack, service = item["stack"], item["service"]
        if dep["depName"] != service["image"] or dep["currentDigest"] != service["current_digest"]:
            raise ContractError(f"{package_file} identity or current digest does not match the request")

        limitations = [warning["message"] for warning in dep["warnings"]]
        selected = _select_update(service, dep["updates"])
        major_alternatives = [
            {"version": update["newValue"], "digest": update.get("newDigest")}
            for update in dep["updates"]
            if update["updateType"] == "major"
        ]
        status = "lookup-failed" if limitations else ("candidate" if selected else "no-update")
        proposed_reference = None
        if selected:
            if service["track"]["kind"] == "floating-tag":
                proposed_reference = f"{service['image']}:{selected['newValue']}@{selected['newDigest']}"
            else:
                proposed_reference = f"{service['image']}:{selected['newValue']}"
        observations.append(
            {
                "identity": {"host": stack["host"], "stack": stack["stack"], "service": service["service"]},
                "tracking_mode": stack["tracking_mode"],
                "status": status,
                "current": {
                    "effective_reference": service["current_effective_reference"],
                    "digest": dep["currentDigest"],
                },
                "candidate_effective_reference": service.get("candidate_effective_reference"),
                "selected_candidate": None if not selected else {
                    "version": selected["newValue"],
                    "digest": selected.get("newDigest"),
                    "update_type": selected["updateType"],
                    "proposed_reference": proposed_reference,
                },
                "visible_major_alternatives": major_alternatives,
                "upstream_links": [value for value in (dep.get("sourceUrl"), dep.get("homepage")) if value],
                "lookup_limitations": limitations,
                "vendor": stack.get("vendor"),
            }
        )

    missing = sorted(set(expected) - seen)
    if missing:
        raise ContractError(f"Renovate omitted projections: {', '.join(missing)}")

    stacks = []
    for stack in request["stacks"]:
        members = [item for item in observations if item["identity"]["host"] == stack["host"] and item["identity"]["stack"] == stack["stack"]]
        stacks.append({
            "host": stack["host"],
            "stack": stack["stack"],
            "scan_status": "incomplete" if any(item["status"] == "lookup-failed" for item in members) else "complete",
            "observation_count": len(members),
        })

    return {
        "schema_version": 1,
        "provenance": {
            "adapter_version": ADAPTER_VERSION,
            "renovate_version": renovate_version,
            "request_fingerprint": request_fingerprint,
            "scan_time": scan_time,
            "scope": [{"host": stack["host"], "stack": stack["stack"]} for stack in request["stacks"]],
        },
        "stacks": stacks,
        "observations": observations,
    }
