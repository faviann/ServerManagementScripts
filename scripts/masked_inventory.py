#!/usr/bin/env python3
"""Mask vault-derived inventory values while preserving diagnostic shape."""

from __future__ import annotations

import re
import sys

import yaml


_VISIBLE_WHITESPACE = {" ": "·", "\n": r"\n", "\t": r"\t"}
_LONG_MASK_RUN = re.compile(r"([a9])\1{16,}")


def mask_value(value: str) -> str:
    """Render a value without exposing its letters, digits, or whitespace."""
    masked_characters: list[str] = []
    for character in value:
        if character in _VISIBLE_WHITESPACE:
            masked_characters.append(_VISIBLE_WHITESPACE[character])
        elif character.isalpha():
            masked_characters.append("a")
        elif character.isdigit():
            masked_characters.append("9")
        else:
            masked_characters.append(character)
    masked = "".join(masked_characters)
    return _LONG_MASK_RUN.sub(
        lambda match: f"{match.group(1)}×{len(match.group())}",
        masked,
    )


def main() -> int:
    """Mask vault-derived values in one merged-host inventory document."""
    inventory = yaml.safe_load(sys.stdin) or {}
    if not isinstance(inventory, dict):
        raise ValueError("merged inventory output must be a mapping")

    vault_values = tuple(
        str(value)
        for key, value in inventory.items()
        if key.startswith("vault_")
        and not isinstance(value, (dict, list))
        and len(str(value)) >= 8
    )
    for key, value in inventory.items():
        if key.startswith("vault_") or (
            isinstance(value, str)
            and any(vault_value in value for vault_value in vault_values)
        ):
            inventory[key] = mask_value(str(value))

    yaml.safe_dump(inventory, sys.stdout, allow_unicode=True, sort_keys=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
