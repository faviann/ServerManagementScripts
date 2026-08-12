from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "scripts" / "image_update_renovate_adapter.py"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def request(*stacks: dict) -> dict:
    return {"schema_version": 1, "stacks": list(stacks)}


def stack(name: str, image: str = "alpine") -> dict:
    return {
        "host": "apps",
        "stack": name,
        "tracking_mode": "image",
        "services": [{
            "service": "app",
            "image": image,
            "current_effective_reference": f"{image}:3.20@{DIGEST_A}",
            "current_digest": DIGEST_A,
            "track": {"kind": "floating-tag", "value": "3.20"},
        }],
    }


def run_adapter(tmp_path: Path, value: dict, fake_body: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(value))
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    fake = bin_path / "npx"
    fake.write_text("#!/usr/bin/env python3\n" + fake_body)
    fake.chmod(0o755)
    capture = tmp_path / "capture.json"
    env = os.environ | {
        "PATH": f"{bin_path}:{os.environ['PATH']}",
        "ADAPTER_TEST_CAPTURE": str(capture),
        "RENOVATE_TOKEN": "ambient-placeholder",
        "RENOVATE_CONFIG": "ambient-placeholder",
        "RENOVATE_X_CUSTOM_ENDPOINT": "ambient-placeholder",
    }
    completed = subprocess.run(
        [sys.executable, str(ADAPTER), str(request_path)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed, json.loads(capture.read_text()) if capture.exists() else {}


FAKE_SUCCESS = r'''
import glob, hashlib, json, os, pathlib, sys
if pathlib.Path(os.environ["ADAPTER_TEST_CAPTURE"]).exists():
    raise SystemExit(99)
files = sorted(glob.glob("projections/*/*/*/compose.yaml"))
deps = []
for filename in files:
    text = pathlib.Path(filename).read_text()
    ref = text.split("image: ", 1)[1].strip()
    name_tag, digest = ref.split("@")
    name, tag = name_tag.rsplit(":", 1)
    warnings = [{"message": "lookup unavailable"}] if "failed" in filename else []
    deps.append({"packageFile": filename, "deps": [{
        "depName": name, "currentValue": tag, "currentDigest": digest,
        "datasource": "docker", "updates": [{"updateType": "digest", "newValue": tag, "newDigest": "sha256:" + "b" * 64}],
        "warnings": warnings, "sourceUrl": "https://example.test/source", "homepage": "https://example.test/home"
    }]})
pathlib.Path(os.environ["ADAPTER_TEST_CAPTURE"]).write_text(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "git": pathlib.Path(".git").exists(), "files": {f: pathlib.Path(f).read_text() for f in files}, "config": json.loads(pathlib.Path("renovate.json").read_text()), "renovate_environment_keys": sorted(key for key in os.environ if key.startswith("RENOVATE_")), "log_environment": {key: os.environ.get(key) for key in ("LOG_LEVEL", "LOG_FORMAT")}}))
print(json.dumps({"level": 30, "msg": "Renovate started", "renovateVersion": "44.5.0"}))
print(json.dumps({"level": 20, "msg": "packageFiles with updates", "config": {"docker-compose": deps}}))
'''


def test_command_returns_normalized_observation_and_uses_one_exact_isolated_lookup(tmp_path: Path) -> None:
    value = request(stack("web"))
    completed, capture = run_adapter(tmp_path, value, FAKE_SUCCESS)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["schema_version"] == 1
    assert result["stacks"] == [{"host": "apps", "stack": "web", "scan_status": "complete", "observation_count": 1}]
    assert result["observations"][0] == {
        "identity": {"host": "apps", "stack": "web", "service": "app"},
        "tracking_mode": "image",
        "status": "candidate",
        "current": {"effective_reference": f"alpine:3.20@{DIGEST_A}", "digest": DIGEST_A},
        "candidate": {"effective_reference": f"alpine:3.20@{DIGEST_B}", "digest": DIGEST_B, "update_type": "digest", "proposed_exact_reference": f"alpine:3.20@{DIGEST_B}"},
        "visible_major_alternatives": [],
        "upstream_links": {"source": "https://example.test/source", "homepage": "https://example.test/home"},
        "limitations": [],
        "vendor": None,
    }
    assert capture["argv"] == ["--yes", "--package", "renovate@44.5.0", "renovate", "--platform=local", "--dry-run=lookup", "--onboarding=false", "--require-config=ignored"]
    assert capture["renovate_environment_keys"] == ["RENOVATE_CONFIG_FILE"]
    assert capture["log_environment"] == {"LOG_LEVEL": "debug", "LOG_FORMAT": "json"}
    assert capture["git"] is False
    assert capture["files"] == {f"projections/apps/web/app/compose.yaml": f"services:\n  app:\n    image: alpine:3.20@{DIGEST_A}\n"}
    assert capture["config"] == {
        "$schema": "https://docs.renovatebot.com/renovate-schema.json",
        "enabledManagers": ["docker-compose"],
        "packageRules": [
            {"matchDatasources": ["docker"], "pinDigests": True, "separateMajorMinor": True},
            {"matchFileNames": ["projections/apps/web/app/compose.yaml"], "allowedVersions": "/^3\\.20$/"},
        ],
    }
    assert not Path(capture["cwd"]).exists()


def test_lookup_warning_is_incomplete_only_for_its_stack(tmp_path: Path) -> None:
    completed, _ = run_adapter(tmp_path, request(stack("healthy"), stack("failed")), FAKE_SUCCESS)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert [item["scan_status"] for item in result["stacks"]] == ["complete", "incomplete"]
    assert [item["status"] for item in result["observations"]] == ["candidate", "lookup-failed"]
    failed = next(item for item in result["observations"] if item["status"] == "lookup-failed")
    assert failed["candidate"] is None
    assert failed["limitations"] == [{"kind": "dependency-lookup-warning", "message": "lookup unavailable"}]


def test_image_exact_version_uses_readable_selected_reference_and_separate_digest(tmp_path: Path) -> None:
    value = request(stack("web"))
    service = value["stacks"][0]["services"][0]
    service["current_effective_reference"] = f"alpine:3.19@{DIGEST_A}"
    service["track"] = {"kind": "exact-version", "value": "3.20"}
    body = FAKE_SUCCESS.replace(
        '[{"updateType": "digest", "newValue": tag, "newDigest": "sha256:" + "b" * 64}]',
        '[{"updateType": "minor", "newValue": "3.20", "newDigest": "sha256:" + "b" * 64}]',
    )

    completed, _ = run_adapter(tmp_path, value, body)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["observations"][0]["candidate"] == {
        "effective_reference": "alpine:3.20",
        "digest": DIGEST_B,
        "update_type": "minor",
        "proposed_exact_reference": "alpine:3.20",
    }


def test_vendor_exact_track_selects_requested_tag_and_keeps_major_alternative(tmp_path: Path) -> None:
    value = request({
        "host": "media",
        "stack": "photos",
        "tracking_mode": "vendor",
        "vendor": {"repository": "https://github.com/example/photos", "candidate_commit": "abc123"},
        "services": [{
            "service": "server",
            "image": "example/photos",
            "current_effective_reference": f"example/photos:2.5.0@{DIGEST_A}",
            "candidate_effective_reference": "example/photos:2.7.5",
            "current_digest": DIGEST_A,
            "track": {"kind": "exact-version", "value": "2.7.5"},
        }],
    })
    body = FAKE_SUCCESS.replace(
        '[{"updateType": "digest", "newValue": tag, "newDigest": "sha256:" + "b" * 64}]',
        '[{"updateType": "minor", "newValue": "2.7.5", "newDigest": "sha256:" + "b" * 64}, {"updateType": "major", "newValue": "3.1.0", "newDigest": "sha256:" + "c" * 64}]',
    )
    completed, capture = run_adapter(tmp_path, value, body)

    assert completed.returncode == 0, completed.stderr
    observation = json.loads(completed.stdout)["observations"][0]
    assert observation["candidate"] == {
        "effective_reference": "example/photos:2.7.5",
        "digest": DIGEST_B,
        "update_type": "minor",
        "proposed_exact_reference": "example/photos:2.7.5",
    }
    assert observation["visible_major_alternatives"] == [{
        "effective_reference": f"example/photos:3.1.0@sha256:{'c' * 64}",
        "version": "3.1.0",
        "digest": "sha256:" + "c" * 64,
    }]
    assert observation["vendor"] == {"repository": "https://github.com/example/photos", "candidate_commit": "abc123"}
    assert capture["config"]["packageRules"][-1] == {
        "matchFileNames": ["projections/media/photos/server/compose.yaml"],
        "allowedVersions": "/^2\\.7\\.5$/",
    }


def test_vendor_candidate_embedded_digest_must_match_renovate_selection(tmp_path: Path) -> None:
    value = request({
        "host": "media",
        "stack": "photos",
        "tracking_mode": "vendor",
        "vendor": {"repository": "https://github.com/example/photos", "candidate_commit": "abc123"},
        "services": [{
            "service": "server",
            "image": "example/photos",
            "current_effective_reference": f"example/photos:2.5.0@{DIGEST_A}",
            "candidate_effective_reference": f"example/photos:2.7.5@sha256:{'c' * 64}",
            "current_digest": DIGEST_A,
            "track": {"kind": "exact-version", "value": "2.7.5"},
        }],
    })
    body = FAKE_SUCCESS.replace(
        '[{"updateType": "digest", "newValue": tag, "newDigest": "sha256:" + "b" * 64}]',
        '[{"updateType": "minor", "newValue": "2.7.5", "newDigest": "sha256:" + "b" * 64}]',
    )
    completed, _ = run_adapter(tmp_path, value, body)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "candidate effective reference" in completed.stderr


def test_vendor_candidate_tag_must_match_renovate_selection(tmp_path: Path) -> None:
    value = request({
        "host": "media",
        "stack": "photos",
        "tracking_mode": "vendor",
        "vendor": {"repository": "https://github.com/example/photos", "candidate_commit": "abc123"},
        "services": [{
            "service": "server",
            "image": "example/photos",
            "current_effective_reference": f"example/photos:2.5.0@{DIGEST_A}",
            "candidate_effective_reference": "example/photos:2.7.5",
            "current_digest": DIGEST_A,
            "track": {"kind": "major-line", "value": 2},
        }],
    })
    body = FAKE_SUCCESS.replace(
        '[{"updateType": "digest", "newValue": tag, "newDigest": "sha256:" + "b" * 64}]',
        '[{"updateType": "minor", "newValue": "2.8.0", "newMajor": 2, "newDigest": "sha256:" + "b" * 64}]',
    )

    completed, _ = run_adapter(tmp_path, value, body)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "candidate effective reference tag disagrees with Renovate selection" in completed.stderr


@pytest.mark.parametrize(
    "change",
    ["bad-version", "missing-file", "extra-file", "wrong-digest", "duplicate-batch", "bad-cardinality", "missing-field", "bad-link"],
)
def test_structural_contract_failures_invalidate_the_batch(tmp_path: Path, change: str) -> None:
    body = FAKE_SUCCESS
    if change == "bad-version":
        body = body.replace('"44.5.0"', '"44.6.0"')
    elif change == "missing-file":
        body = body.replace('"docker-compose": deps', '"docker-compose": []')
    elif change == "extra-file":
        body = body.replace('"docker-compose": deps', '"docker-compose": deps + [{"packageFile": "projections/extra/x/y/compose.yaml", "deps": []}]')
    elif change == "wrong-digest":
        body = body.replace('"currentDigest": digest', '"currentDigest": "sha256:" + "c" * 64')
    elif change == "duplicate-batch":
        body += '\nprint(json.dumps({"level": 20, "msg": "packageFiles with updates", "config": {"docker-compose": deps}}))\n'
    elif change == "bad-cardinality":
        body = body.replace('"deps": [{', '"deps": [{"depName": "extra"}, {')
    elif change == "missing-field":
        body = body.replace('"datasource": "docker", ', "")
    else:
        body = body.replace('"sourceUrl": "https://example.test/source"', '"sourceUrl": 42')
    completed, _ = run_adapter(tmp_path, request(stack("web")), body)
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "batch invalid" in completed.stderr


def test_projection_mutation_invalidates_the_batch(tmp_path: Path) -> None:
    body = FAKE_SUCCESS.replace(
        'pathlib.Path(os.environ["ADAPTER_TEST_CAPTURE"]).write_text',
        'pathlib.Path(files[0]).write_text("changed")\npathlib.Path(os.environ["ADAPTER_TEST_CAPTURE"]).write_text',
    )
    completed, _ = run_adapter(tmp_path, request(stack("web")), body)
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "projection mutation" in completed.stderr


def test_unexpected_started_record_level_invalidates_the_batch(tmp_path: Path) -> None:
    body = FAKE_SUCCESS.replace(
        '{"level": 30, "msg": "Renovate started"',
        '{"level": 20, "msg": "Renovate started"',
    )
    completed, _ = run_adapter(tmp_path, request(stack("web")), body)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "started record" in completed.stderr


def test_unknown_renovate_update_type_invalidates_the_batch(tmp_path: Path) -> None:
    body = FAKE_SUCCESS.replace('"updateType": "digest"', '"updateType": "replacement"')
    completed, _ = run_adapter(tmp_path, request(stack("web")), body)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "updateType" in completed.stderr


def test_visible_major_without_digest_invalidates_the_batch(tmp_path: Path) -> None:
    body = FAKE_SUCCESS.replace(
        '[{"updateType": "digest", "newValue": tag, "newDigest": "sha256:" + "b" * 64}]',
        '[{"updateType": "major", "newValue": "4.0.0", "newMajor": 4}]',
    )
    completed, _ = run_adapter(tmp_path, request(stack("web")), body)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "comparable digest" in completed.stderr


def test_request_schema_failure_happens_before_renovate(tmp_path: Path) -> None:
    completed, capture = run_adapter(tmp_path, {"schema_version": 2, "stacks": []}, FAKE_SUCCESS)
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert capture == {}
    assert "request schema" in completed.stderr


def test_vendor_tracking_requires_candidate_reference_before_renovate(tmp_path: Path) -> None:
    value = request(stack("photos", image="example/photos"))
    value["stacks"][0].update({
        "tracking_mode": "vendor",
        "vendor": {"repository": "https://github.com/example/photos", "candidate_commit": "abc123"},
    })

    completed, capture = run_adapter(tmp_path, value, FAKE_SUCCESS)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert capture == {}
    assert "vendor tracking requires candidate_effective_reference" in completed.stderr


def test_image_tracking_rejects_candidate_reference_before_renovate(tmp_path: Path) -> None:
    value = request(stack("web"))
    value["stacks"][0]["services"][0]["candidate_effective_reference"] = "alpine:3.20"

    completed, capture = run_adapter(tmp_path, value, FAKE_SUCCESS)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert capture == {}
    assert "image tracking prohibits candidate_effective_reference" in completed.stderr


@pytest.mark.parametrize("kind", ["floating-tag", "exact-version", "major-line"])
def test_command_rejects_empty_track_value_before_renovate(tmp_path: Path, kind: str) -> None:
    value = request(stack("web"))
    value["stacks"][0]["services"][0]["track"] = {"kind": kind, "value": ""}

    completed, capture = run_adapter(tmp_path, value, FAKE_SUCCESS)

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert capture == {}
    assert "request schema track value must be a non-empty string or integer" in completed.stderr


def test_renovate_process_failure_invalidates_without_leaking_raw_output(tmp_path: Path) -> None:
    body = 'import sys\nprint("raw secret-like diagnostic")\nraise SystemExit(7)\n'
    completed, _ = run_adapter(tmp_path, request(stack("web")), body)
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "raw secret-like diagnostic" not in completed.stderr
    assert "Renovate process exited 7" in completed.stderr
