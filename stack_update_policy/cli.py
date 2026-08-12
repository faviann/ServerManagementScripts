from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .validation import validate_stack


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stack-update-policy")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--repository-root", required=True, type=Path)
    validate.add_argument("identity")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    validation = validate_stack(args.repository_root, args.identity)
    print(json.dumps(validation.as_dict(), sort_keys=True, separators=(",", ":")))
    if validation.valid:
        print(f"validated {args.identity}", file=sys.stderr)
        return 0
    for error in validation.errors:
        print(f"{error.path}: {error.message}", file=sys.stderr)
    return 1
