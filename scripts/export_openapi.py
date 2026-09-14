"""Export canonical OpenAPI + SHA manifest from app.main:create_app."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.contracts.openapi import SOURCE_APP, export_openapi
from app.core.config import Settings
from app.main import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export canonical OpenAPI from the real FastAPI app."
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Write openapi.canonical.json and openapi.manifest.json here",
    )
    parser.add_argument(
        "--app-env",
        choices=("test", "dev", "prod"),
        default="test",
        help="Settings.app_env for create_app (default: test)",
    )
    args = parser.parse_args(argv)

    application = create_app(Settings(app_env=args.app_env))
    exported = export_openapi(application, repo_root=Path.cwd())
    if args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / "openapi.canonical.json").write_bytes(exported.canonical_bytes)
        (args.out_dir / "openapi.manifest.json").write_text(
            json.dumps(exported.manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"source_app={SOURCE_APP}")
    print(f"openapi_sha256={exported.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
