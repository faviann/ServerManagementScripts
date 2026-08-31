"""Public contract tests for masked inventory values."""

from pathlib import Path
import sys

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.masked_inventory import mask_value  # noqa: E402


def test_mask_value_preserves_diagnostic_shape_without_case() -> None:
    assert mask_value('AbZ09$/,_" \n\t') == 'aaa99$/,_"·\\n\\t'


def test_mask_value_collapses_only_runs_longer_than_sixteen() -> None:
    assert mask_value("A" * 16 + "1" * 17) == "a" * 16 + "9×17"


def test_vault_example_keys_have_the_required_prefix() -> None:
    vault_example = yaml.safe_load(
        (REPO_ROOT / "inventory/group_vars/all/vault.yml.example").read_text(
            encoding="utf-8"
        )
    )

    assert vault_example
    assert all(key.startswith("vault_") for key in vault_example)
