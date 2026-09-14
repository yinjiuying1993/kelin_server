"""Canonical OpenAPI export from the real FastAPI app. Spec §20.1.

The SHA is of the canonical JSON, never of the manifest. Do not hand-write a
spec that diverges from create_app().
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.core.config import Settings
from app.core.envelope import utc_server_time

SOURCE_APP = "app.main:create_app"
VOLATILE_TOP_LEVEL = frozenset({"servers"})


def _canonicalize(value: Any, *, strip_volatile: bool = False) -> Any:
    if isinstance(value, dict):
        items = sorted(value.items())
        return {
            key: _canonicalize(item)
            for key, item in items
            if not (strip_volatile and key in VOLATILE_TOP_LEVEL)
        }
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value


def canonical_openapi_bytes(document: dict[str, Any]) -> bytes:
    canonical = _canonicalize(document, strip_volatile=True)
    return json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def openapi_sha256(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_openapi_bytes(document)).hexdigest()


def _source_commit(repo_root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    sha = completed.stdout.strip()
    return sha or None


@dataclass(frozen=True)
class OpenApiExport:
    document: dict[str, Any]
    canonical_bytes: bytes
    sha256: str
    manifest: dict[str, Any]


def export_openapi(application: FastAPI, *, repo_root: Path | None = None) -> OpenApiExport:
    raw = application.openapi()
    if not isinstance(raw, dict):
        raise TypeError("FastAPI openapi() must return an object")
    canonical = canonical_openapi_bytes(raw)
    digest = hashlib.sha256(canonical).hexdigest()
    parsed: Any = json.loads(canonical.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise TypeError("canonical OpenAPI must be an object")
    settings = getattr(application.state, "settings", None)
    api_version = "v1"
    schema_version = 2
    if isinstance(settings, Settings):
        api_version = settings.api_version
        schema_version = settings.schema_version
    manifest: dict[str, Any] = {
        "api_version": api_version,
        "schema_version": schema_version,
        "openapi_sha256": digest,
        "generated_at": utc_server_time(),
        "source_app": SOURCE_APP,
        "source_commit": _source_commit(repo_root or Path.cwd()),
    }
    return OpenApiExport(
        document=parsed,
        canonical_bytes=canonical,
        sha256=digest,
        manifest=manifest,
    )
