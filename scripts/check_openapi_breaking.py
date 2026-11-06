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
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the breaking report to this file (stdout is still printed)",
    )
    parser.add_argument(
        "--note",
        action="append",
        default=[],
        help="Header note written above the report in --out (repeatable)",
    )
    args = parser.parse_args(argv)
    old = json.loads(args.old.read_text(encoding="utf-8"))
    new = json.loads(args.new.read_text(encoding="utf-8"))
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise TypeError("OpenAPI documents must be objects")
    changes = find_breaking_changes(old, new)
    report = format_breaking_report(changes)
    print(report)
    if args.out is not None:
        lines = [str(item) for item in args.note]
        if lines:
            lines.append("")
        lines.append(report)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return 1 if changes else 0


if __name__ == "__main__":
    raise SystemExit(main())
