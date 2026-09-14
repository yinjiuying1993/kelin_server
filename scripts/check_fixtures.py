"""Check fixture harness against the live FastAPI OpenAPI SHA."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.contracts.fixtures import (
    FixtureError,
    assert_manifest_matches_export,
    catalog_for_openapi,
    evaluate_catalog,
    load_manifest,
    write_fixture_manifest,
)
from app.contracts.openapi import export_openapi
from app.core.config import Settings
from app.main import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Envelope fixtures and OpenAPI SHA manifest."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("fixtures"),
        help="Fixture root directory (default: fixtures)",
    )
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="Rewrite manifest.json from the live app export",
    )
    args = parser.parse_args(argv)
    exported = export_openapi(create_app(Settings(app_env="test")), repo_root=Path.cwd())
    if args.write_manifest:
        write_fixture_manifest(args.root, exported)
    try:
        entries = catalog_for_openapi(exported.document)
        evaluate_catalog(args.root, entries)
        assert_manifest_matches_export(load_manifest(args.root), exported)
    except FixtureError as exc:
        print(f"fixture_check_failed: {exc}")
        return 1
    print(f"openapi_sha256={exported.sha256}")
    print(f"fixture_entries={len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
