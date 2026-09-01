#!/usr/bin/env python3
"""Regression test: prerequisite checks must not poison the target's fact cache.

Issue #225: `controller-prerequisites.yml` runs `hosts: <lifecycle target>` with
`connection: local` because the checks inspect the control node. With a cold or
expired fact cache the first module invocation triggers interpreter discovery
for that host over the local connection, so the controller's venv interpreter is
recorded as a fact *of the target* and persisted. The next play, which really
does connect over SSH, then tries to execute the controller's venv python inside
the container and fails as an opaque `timed out waiting for ping module test`.

The play is exercised against a remote-SSH target identity with a cold jsonfile
cache. Whatever the play caches for that identity must not carry a control-node
interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ansible_test_helper import ansible_playbook_command


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
INVENTORY = FIXTURES / "controller_prerequisite_target_inventory.yml"
VAULT_PASSWORD_FILE = REPO_ROOT / "tests" / "fixtures" / "ansible" / "vault-pass"
PLAYBOOK = REPO_ROOT / "playbooks" / "controller-prerequisites.yml"
TARGET = "prerequisite_cache_target"
POISONED_FACT = "discovered_interpreter_python"
ANSIBLE_PLAYBOOK = ansible_playbook_command(supplies_own_inventory=True)


def run_prerequisites(cache_connection: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "ANSIBLE_INVENTORY": str(INVENTORY),
            "ANSIBLE_VAULT_PASSWORD_FILE": str(VAULT_PASSWORD_FILE),
            "ANSIBLE_CACHE_PLUGIN": "jsonfile",
            "ANSIBLE_CACHE_PLUGIN_CONNECTION": str(cache_connection),
            # The wrapper assertion is about serialized lifecycle mutation, not
            # about the caching behaviour under test.
            "HOMELAB_IAC_LIFECYCLE_WRAPPER": "1",
        }
    )
    return subprocess.run(
        [
            *ANSIBLE_PLAYBOOK,
            str(PLAYBOOK),
            "-e",
            f"prerequisite_target_pattern={TARGET}",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def cached_entries(cache_connection: Path) -> dict[str, str]:
    if not cache_connection.is_dir():
        return {}
    return {
        entry.name: entry.read_text(encoding="utf-8")
        for entry in sorted(cache_connection.iterdir())
        if entry.is_file()
    }


def decoded_facts(contents: str) -> dict[str, object]:
    """Return the cached facts, unwrapping the jsonfile plugin's payload string."""
    try:
        facts = json.loads(contents)
    except json.JSONDecodeError:
        return {"<unparsable>": contents}
    if not isinstance(facts, dict):
        return {}
    payload = facts.get("__payload__")
    if isinstance(payload, str):
        try:
            facts = json.loads(payload)
        except json.JSONDecodeError:
            return {"<unparsable payload>": payload}
    if not isinstance(facts, dict):
        return {}
    return facts


def poisoned_facts(contents: str) -> dict[str, object]:
    if POISONED_FACT not in contents:
        return {}
    facts = decoded_facts(contents)
    poisoned = {key: value for key, value in facts.items() if POISONED_FACT in key}
    # The marker is in the entry even if the payload shape changed; report the
    # whole entry rather than silently passing.
    return poisoned or {"<entry>": contents}


def main() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="controller-prerequisite-cache-") as root:
        cache_connection = Path(root) / "fact-cache"
        proc = run_prerequisites(cache_connection)
        output = f"{proc.stdout}\n{proc.stderr}"

        if proc.returncode != 0:
            failures.append("controller prerequisite play failed unexpectedly")
            failures.append(output)
        elif f"ok: [{TARGET}]" not in proc.stdout:
            # A pattern that matches nothing still exits 0, and would make the
            # cache assertion below vacuously true.
            failures.append(
                f"controller prerequisite play never ran against {TARGET}"
            )
            failures.append(output)
        else:
            entries = cached_entries(cache_connection)
            for name, contents in entries.items():
                poisoned = poisoned_facts(contents)
                if poisoned:
                    failures.append(
                        "controller prerequisite run persisted control-node "
                        f"interpreter facts into fact cache entry {name}: "
                        f"{json.dumps(poisoned, sort_keys=True)}"
                    )
            if failures:
                failures.append(output)

    if failures:
        for line in failures:
            print(line, file=sys.stderr)
        return 1

    print(
        "ok: controller prerequisite checks leave no control-node interpreter "
        f"in a cold fact cache for remote target {TARGET}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
