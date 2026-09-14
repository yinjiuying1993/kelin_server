from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.contracts.breaking import BreakingChange, BreakingKind, find_breaking_changes
from app.contracts.openapi import export_openapi
from app.core.config import Settings
from app.main import create_app
from scripts.check_openapi_breaking import main as check_main


def _real_spec() -> dict[str, Any]:
    return deepcopy(export_openapi(create_app(Settings(app_env="test"))).document)


def _probe_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["id", "kind"],
        "properties": {
            "id": {"type": "string"},
            "kind": {"type": "string", "enum": ["alpha", "beta"]},
        },
    }


def _spec_copy_with_probe() -> dict[str, Any]:
    document = _real_spec()
    components = document.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas["ContractProbe"] = _probe_schema()
    return document


def _kinds_at(changes: list[BreakingChange], kind: BreakingKind, needle: str) -> list[str]:
    return [
        f"{item.location} {item.detail}"
        for item in changes
        if item.kind == kind and needle in f"{item.location} {item.detail}"
    ]


def test_identical_real_app_export_is_not_breaking() -> None:
    spec = _real_spec()
    assert find_breaking_changes(spec, deepcopy(spec)) == []


def test_description_only_change_is_not_breaking() -> None:
    old = _real_spec()
    new = deepcopy(old)
    new["paths"]["/health/live"]["get"]["summary"] = "live probe renamed"
    new["info"]["description"] = "cosmetic"
    assert find_breaking_changes(old, new) == []


def test_deleting_required_field_in_spec_copy_is_breaking() -> None:
    old = _spec_copy_with_probe()
    new = deepcopy(old)
    schema = new["components"]["schemas"]["ContractProbe"]
    del schema["properties"]["id"]
    schema["required"] = ["kind"]
    changes = find_breaking_changes(old, new)
    assert changes, "deleting a required field must fail the breaking check"
    assert _kinds_at(changes, BreakingKind.REQUIRED_REMOVED, "id")
    assert _kinds_at(changes, BreakingKind.FIELD_REMOVED, "id")


def test_type_change_in_spec_copy_is_breaking() -> None:
    old = _spec_copy_with_probe()
    new = deepcopy(old)
    new["components"]["schemas"]["ContractProbe"]["properties"]["id"]["type"] = "integer"
    changes = find_breaking_changes(old, new)
    assert changes, "changing a field type must fail the breaking check"
    assert _kinds_at(changes, BreakingKind.TYPE_CHANGED, "id")


def test_enum_narrowing_in_spec_copy_is_breaking() -> None:
    old = _spec_copy_with_probe()
    new = deepcopy(old)
    new["components"]["schemas"]["ContractProbe"]["properties"]["kind"]["enum"] = ["alpha"]
    changes = find_breaking_changes(old, new)
    assert changes, "narrowing an enum must fail the breaking check"
    assert _kinds_at(changes, BreakingKind.ENUM_NARROWED, "beta")


def test_enum_widening_in_spec_copy_is_breaking() -> None:
    old = _spec_copy_with_probe()
    new = deepcopy(old)
    new["components"]["schemas"]["ContractProbe"]["properties"]["kind"]["enum"] = [
        "alpha",
        "beta",
        "gamma",
    ]
    changes = find_breaking_changes(old, new)
    assert changes, "widening an enum must fail the breaking check"
    assert _kinds_at(changes, BreakingKind.ENUM_WIDENED, "gamma")


def test_type_change_on_real_health_live_schema_is_breaking() -> None:
    old = _real_spec()
    new = deepcopy(old)
    schema = new["paths"]["/health/live"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    schema["additionalProperties"]["type"] = "integer"
    changes = find_breaking_changes(old, new)
    assert changes, "changing the live health schema type must fail the breaking check"
    assert _kinds_at(changes, BreakingKind.TYPE_CHANGED, "additionalProperties")


def test_status_code_change_on_real_health_path_is_breaking() -> None:
    old = _real_spec()
    new = deepcopy(old)
    del new["paths"]["/health/live"]["get"]["responses"]["200"]
    new["paths"]["/health/live"]["get"]["responses"]["204"] = {"description": "empty"}
    changes = find_breaking_changes(old, new)
    assert changes, "changing status codes must fail the breaking check"
    assert _kinds_at(changes, BreakingKind.STATUS_CODE_CHANGED, "200")
    assert _kinds_at(changes, BreakingKind.STATUS_CODE_CHANGED, "204")


def test_cli_exits_nonzero_for_breaking_sample(tmp_path: Path) -> None:
    old = _spec_copy_with_probe()
    new = deepcopy(old)
    del new["components"]["schemas"]["ContractProbe"]["properties"]["id"]
    new["components"]["schemas"]["ContractProbe"]["required"] = ["kind"]
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(json.dumps(old), encoding="utf-8")
    new_path.write_text(json.dumps(new), encoding="utf-8")
    assert check_main(["--old", str(old_path), "--new", str(new_path)]) == 1
    assert check_main(["--old", str(old_path), "--new", str(old_path)]) == 0
