"""Compare two OpenAPI documents and fail on breaking changes. Spec §20.1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.contracts.breaking import find_breaking_changes, format_breaking_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if the new OpenAPI document has breaking changes vs the old one."
    )
    parser.add_argument("--old", type=Path, required=True, help="Baseline OpenAPI JSON")
    parser.add_argument("--new", type=Path, required=True, help="Candidate OpenAPI JSON")
    args = parser.parse_args(argv)
    old = json.loads(args.old.read_text(encoding="utf-8"))
    new = json.loads(args.new.read_text(encoding="utf-8"))
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise TypeError("OpenAPI documents must be objects")
    changes = find_breaking_changes(old, new)
    print(format_breaking_report(changes))
    return 1 if changes else 0


if __name__ == "__main__":
    raise SystemExit(main())
