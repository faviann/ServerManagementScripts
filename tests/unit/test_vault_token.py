#!/usr/bin/env python3
"""Tests for token handling in authentik_blueprint_sync."""

from __future__ import annotations

import unittest
from pathlib import Path

import importlib.util
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "authentik_blueprint_sync.py"

def load_script():
    spec = importlib.util.spec_from_file_location("authentik_blueprint_sync", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load script from {SCRIPT_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["authentik_blueprint_sync"] = mod
    spec.loader.exec_module(mod)
    return mod


class ExtractVaultTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_script()

    def test_extracts_token_from_yaml_string(self):
        yaml_str = "vault_auth_blueprint_api_token: mytoken123\nother_key: other_value\n"
        token = self.mod._extract_vault_token(yaml_str, "vault_auth_blueprint_api_token")
        self.assertEqual(token, "mytoken123")

    def test_raises_on_missing_key(self):
        yaml_str = "some_other_key: somevalue\n"
        with self.assertRaises(KeyError):
            self.mod._extract_vault_token(yaml_str, "vault_auth_blueprint_api_token")

    def test_strips_whitespace_from_token(self):
        yaml_str = "vault_auth_blueprint_api_token: '  spaced  '\n"
        token = self.mod._extract_vault_token(yaml_str, "vault_auth_blueprint_api_token")
        self.assertEqual(token, "spaced")


class PublicCliTokenFileTests(unittest.TestCase):
    def test_token_file_is_required_for_every_command(self):
        for command in ("export", "apply"):
            with self.subTest(command=command):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT_PATH), command],
                    cwd=REPO_ROOT,
                    env={"HOME": "/nonexistent", "PATH": ""},
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("the following arguments are required: --token-file", result.stderr)


if __name__ == "__main__":
    unittest.main()
