#!/usr/bin/env python3
"""Interactive shell for the throwaway pinned Renovate adapter prototype."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator

from adapter import PINNED_RENOVATE_VERSION, ContractError, normalize


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "scan-request.json"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


def _validate_schema(name: str, value: Any) -> None:
    schema = json.loads((ROOT / "schemas" / name).read_text())
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
    if errors:
        location = ".".join(str(part) for part in errors[0].path) or "root"
        raise ContractError(f"{name} rejected {location}: {errors[0].message}")


def _tag(reference: str) -> str:
    without_digest = reference.split("@", 1)[0]
    final = without_digest.rsplit("/", 1)[-1]
    if ":" not in final:
        raise ContractError(f"fixture reference needs an explicit tag: {reference}")
    return final.rsplit(":", 1)[1]


def _projection_path(stack: dict[str, Any], service: dict[str, Any]) -> str:
    return f"projections/{stack['host']}/{stack['stack']}/{service['service']}/compose.yaml"


def _config(request: dict[str, Any]) -> dict[str, Any]:
    rules: list[dict[str, Any]] = [
        {"matchDatasources": ["docker"], "pinDigests": True, "separateMajorMinor": True}
    ]
    for stack in request["stacks"]:
        for service in stack["services"]:
            kind = service["track"]["kind"]
            if kind in {"floating-tag", "exact-version"}:
                value = str(service["track"]["value"])
                rules.append({
                    "matchFileNames": [_projection_path(stack, service)],
                    "allowedVersions": f"/^{re.escape(value)}$/",
                })
    return {
        "$schema": "https://docs.renovatebot.com/renovate-schema.json",
        "enabledManagers": ["docker-compose"],
        "packageRules": rules,
    }


def _write_scan(root: Path, request: dict[str, Any]) -> dict[str, str]:
    files: dict[str, str] = {}
    for stack in request["stacks"]:
        for service in stack["services"]:
            relative = _projection_path(stack, service)
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            scan_reference = f"{service['image']}:{_tag(service['current_effective_reference'])}@{service['current_digest']}"
            content = f"services:\n  {service['service']}:\n    image: {scan_reference}\n"
            path.write_text(content)
            files[relative] = hashlib.sha256(content.encode()).hexdigest()
    (root / "renovate.json").write_text(json.dumps(_config(request), indent=2) + "\n")
    return files


def _json_records(output: str) -> list[dict[str, Any]]:
    records = []
    for line in output.splitlines():
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def run_scan() -> dict[str, Any]:
    request_text = FIXTURE.read_text()
    request = json.loads(request_text)
    _validate_schema("scan-request.schema.json", request)
    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="renovate-adapter-prototype-") as temp:
        scan_root = Path(temp)
        before = _write_scan(scan_root, request)
        environment = os.environ.copy()
        environment.update({
            "RENOVATE_CONFIG_FILE": "renovate.json",
            "LOG_LEVEL": "debug",
            "LOG_FORMAT": "json",
        })
        command = [
            "npx", "--yes", "--package", f"renovate@{PINNED_RENOVATE_VERSION}", "renovate",
            "--platform=local", "--dry-run=lookup", "--onboarding=false",
        ]
        completed = subprocess.run(command, cwd=scan_root, env=environment, text=True, capture_output=True, check=False)
        records = _json_records(completed.stdout + "\n" + completed.stderr)
        starts = [record for record in records if record.get("msg") == "Renovate started"]
        batches = [record for record in records if record.get("msg") == "packageFiles with updates"]
        if completed.returncode != 0:
            raise ContractError(f"Renovate exited {completed.returncode}")
        if len(starts) != 1 or len(batches) != 1:
            raise ContractError(f"expected one version and one batch record, got {len(starts)} and {len(batches)}")
        _validate_schema("renovate-raw-record.schema.json", batches[0])
        after = {relative: hashlib.sha256((scan_root / relative).read_bytes()).hexdigest() for relative in before}
        if before != after:
            raise ContractError("Renovate changed a scan projection")
        result = normalize(
            request,
            batches[0],
            starts[0].get("renovateVersion"),
            fingerprint,
            datetime.now(timezone.utc).isoformat(),
        )
        result["prototype_evidence"] = {
            "invocation": " ".join(command),
            "plain_non_git_scan_directory": True,
            "projection_hashes_unchanged": True,
            "consumed_batch_records": len(batches),
        }
        _validate_schema("candidate-observations.schema.json", result)
        return result


def _render(title: str, state: Any) -> None:
    print("\033[2J\033[H", end="")
    print(f"{BOLD}{title}{RESET}\n")
    print(json.dumps(state, indent=2))
    print(f"\n{BOLD}[a]{RESET} {DIM}run all fixtures{RESET}  {BOLD}[r]{RESET} {DIM}show request{RESET}  {BOLD}[s]{RESET} {DIM}show schemas{RESET}  {BOLD}[q]{RESET} {DIM}quit{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run every fixture and print normalized JSON")
    args = parser.parse_args()
    if args.once:
        print(json.dumps(run_scan(), indent=2))
        return 0
    state: Any = {"status": "ready", "fixture": str(FIXTURE.relative_to(ROOT))}
    while True:
        _render("Pinned Renovate adapter prototype", state)
        action = input("\n> ").strip().lower()
        try:
            if action == "q":
                return 0
            if action == "a":
                state = run_scan()
            elif action == "r":
                state = json.loads(FIXTURE.read_text())
            elif action == "s":
                state = {path.name: json.loads(path.read_text()) for path in sorted((ROOT / "schemas").glob("*.json"))}
            else:
                state = {"status": "unknown action", "action": action}
        except (ContractError, OSError, json.JSONDecodeError) as error:
            state = {"status": "global scan failure", "error": str(error)}


if __name__ == "__main__":
    raise SystemExit(main())
