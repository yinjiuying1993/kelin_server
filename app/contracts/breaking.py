"""OpenAPI breaking-change analysis. Spec §20.1.

Compares two OpenAPI documents and reports field deletion, required-ness,
type, enum, and status-code changes. Does not invent business endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


class BreakingKind(StrEnum):
    FIELD_REMOVED = "field_removed"
    REQUIRED_REMOVED = "required_removed"
    REQUIRED_ADDED = "required_added"
    TYPE_CHANGED = "type_changed"
    ENUM_NARROWED = "enum_narrowed"
    ENUM_WIDENED = "enum_widened"
    STATUS_CODE_CHANGED = "status_code_changed"
    PATH_REMOVED = "path_removed"
    METHOD_REMOVED = "method_removed"
    SCHEMA_REMOVED = "schema_removed"


@dataclass(frozen=True)
class BreakingChange:
    kind: BreakingKind
    location: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.location}: {self.detail}"


def find_breaking_changes(old: dict[str, Any], new: dict[str, Any]) -> list[BreakingChange]:
    findings: list[BreakingChange] = []
    _compare_paths(old.get("paths"), new.get("paths"), "paths", findings)
    old_components = _mapping(old.get("components"))
    new_components = _mapping(new.get("components"))
    _compare_schemas(
        (old_components or {}).get("schemas"),
        (new_components or {}).get("schemas"),
        "components.schemas",
        findings,
    )
    findings.sort(key=lambda item: (item.kind.value, item.location, item.detail))
    return findings


def format_breaking_report(changes: list[BreakingChange]) -> str:
    if not changes:
        return "no breaking changes"
    return "\n".join(str(item) for item in changes)


def _mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    return None


def _join(parent: str, child: str) -> str:
    if not parent:
        return child
    return f"{parent}.{child}"


def _compare_paths(
    old: Any,
    new: Any,
    location: str,
    findings: list[BreakingChange],
) -> None:
    old_paths = _mapping(old) or {}
    new_paths = _mapping(new) or {}
    for path, old_item in old_paths.items():
        here = _join(location, path)
        if path not in new_paths:
            findings.append(
                BreakingChange(
                    BreakingKind.PATH_REMOVED,
                    here,
                    f"path {path} was removed",
                )
            )
            continue
        _compare_path_item(old_item, new_paths[path], here, findings)


def _compare_path_item(
    old: Any,
    new: Any,
    location: str,
    findings: list[BreakingChange],
) -> None:
    old_item = _mapping(old) or {}
    new_item = _mapping(new) or {}
    for method, old_op in old_item.items():
        if method not in HTTP_METHODS:
            continue
        here = _join(location, method)
        if method not in new_item:
            findings.append(
                BreakingChange(
                    BreakingKind.METHOD_REMOVED,
                    here,
                    f"method {method} was removed",
                )
            )
            continue
        _compare_operation(old_op, new_item[method], here, findings)


def _compare_operation(
    old: Any,
    new: Any,
    location: str,
    findings: list[BreakingChange],
) -> None:
    old_op = _mapping(old) or {}
    new_op = _mapping(new) or {}
    _compare_responses(old_op.get("responses"), new_op.get("responses"), location, findings)
    _compare_schema(
        _request_schema(old_op),
        _request_schema(new_op),
        _join(location, "requestBody"),
        findings,
    )


def _request_schema(operation: dict[str, Any]) -> Any:
    body = _mapping(operation.get("requestBody"))
    if body is None:
        return None
    return _content_schema(body.get("content"))


def _content_schema(content: Any) -> Any:
    mapping = _mapping(content)
    if mapping is None:
        return None
    json_body = _mapping(mapping.get("application/json"))
    if json_body is not None:
        return json_body.get("schema")
    for media in mapping.values():
        media_map = _mapping(media)
        if media_map is not None and "schema" in media_map:
            return media_map["schema"]
    return None


def _compare_responses(
    old: Any,
    new: Any,
    location: str,
    findings: list[BreakingChange],
) -> None:
    old_responses = _mapping(old) or {}
    new_responses = _mapping(new) or {}
    here = _join(location, "responses")
    old_codes = set(old_responses)
    new_codes = set(new_responses)
    removed = sorted(old_codes - new_codes)
    added = sorted(new_codes - old_codes)
    if removed or added:
        detail_parts: list[str] = []
        if removed:
            detail_parts.append(f"removed {', '.join(removed)}")
        if added:
            detail_parts.append(f"added {', '.join(added)}")
        findings.append(
            BreakingChange(
                BreakingKind.STATUS_CODE_CHANGED,
                here,
                "; ".join(detail_parts),
            )
        )
    for code in sorted(old_codes & new_codes):
        old_response = _mapping(old_responses[code]) or {}
        new_response = _mapping(new_responses[code]) or {}
        _compare_schema(
            _content_schema(old_response.get("content")),
            _content_schema(new_response.get("content")),
            _join(here, f"{code}.content"),
            findings,
        )


def _compare_schemas(
    old: Any,
    new: Any,
    location: str,
    findings: list[BreakingChange],
) -> None:
    old_schemas = _mapping(old) or {}
    new_schemas = _mapping(new) or {}
    for name, old_schema in old_schemas.items():
        here = _join(location, name)
        if name not in new_schemas:
            findings.append(
                BreakingChange(
                    BreakingKind.SCHEMA_REMOVED,
                    here,
                    f"schema {name} was removed",
                )
            )
            continue
        _compare_schema(old_schema, new_schemas[name], here, findings)


def _compare_schema(
    old: Any,
    new: Any,
    location: str,
    findings: list[BreakingChange],
) -> None:
    if old is None and new is None:
        return
    old_schema = _mapping(old)
    new_schema = _mapping(new)
    if old_schema is None or new_schema is None:
        if old != new:
            findings.append(
                BreakingChange(
                    BreakingKind.TYPE_CHANGED,
                    location,
                    f"schema changed from {old!r} to {new!r}",
                )
            )
        return

    old_ref = old_schema.get("$ref")
    new_ref = new_schema.get("$ref")
    if old_ref != new_ref and (old_ref is not None or new_ref is not None):
        findings.append(
            BreakingChange(
                BreakingKind.TYPE_CHANGED,
                location,
                f"$ref changed from {old_ref!r} to {new_ref!r}",
            )
        )

    old_type = _schema_type(old_schema)
    new_type = _schema_type(new_schema)
    if old_type != new_type:
        findings.append(
            BreakingChange(
                BreakingKind.TYPE_CHANGED,
                location,
                f"type changed from {old_type} to {new_type}",
            )
        )

    old_required = _string_set(old_schema.get("required"))
    new_required = _string_set(new_schema.get("required"))
    for name in sorted(old_required - new_required):
        findings.append(
            BreakingChange(
                BreakingKind.REQUIRED_REMOVED,
                _join(location, "required"),
                f"required field {name} was removed",
            )
        )
    for name in sorted(new_required - old_required):
        findings.append(
            BreakingChange(
                BreakingKind.REQUIRED_ADDED,
                _join(location, "required"),
                f"required field {name} was added",
            )
        )

    old_props = _mapping(old_schema.get("properties")) or {}
    new_props = _mapping(new_schema.get("properties")) or {}
    for name, old_prop in old_props.items():
        here = _join(location, f"properties.{name}")
        if name not in new_props:
            findings.append(
                BreakingChange(
                    BreakingKind.FIELD_REMOVED,
                    here,
                    f"field {name} was removed",
                )
            )
            continue
        _compare_schema(old_prop, new_props[name], here, findings)

    _compare_enum(old_schema, new_schema, location, findings)

    if "items" in old_schema or "items" in new_schema:
        _compare_schema(
            old_schema.get("items"),
            new_schema.get("items"),
            _join(location, "items"),
            findings,
        )
    if "additionalProperties" in old_schema or "additionalProperties" in new_schema:
        old_extra = old_schema.get("additionalProperties")
        new_extra = new_schema.get("additionalProperties")
        if isinstance(old_extra, dict) or isinstance(new_extra, dict):
            _compare_schema(
                old_extra if isinstance(old_extra, dict) else None,
                new_extra if isinstance(new_extra, dict) else None,
                _join(location, "additionalProperties"),
                findings,
            )
        elif old_extra != new_extra:
            findings.append(
                BreakingChange(
                    BreakingKind.TYPE_CHANGED,
                    _join(location, "additionalProperties"),
                    f"additionalProperties changed from {old_extra!r} to {new_extra!r}",
                )
            )

    for combinator in ("allOf", "anyOf", "oneOf"):
        if combinator in old_schema or combinator in new_schema:
            _compare_schema_list(
                old_schema.get(combinator),
                new_schema.get(combinator),
                _join(location, combinator),
                findings,
            )


def _compare_schema_list(
    old: Any,
    new: Any,
    location: str,
    findings: list[BreakingChange],
) -> None:
    old_list = old if isinstance(old, list) else []
    new_list = new if isinstance(new, list) else []
    if len(old_list) != len(new_list):
        findings.append(
            BreakingChange(
                BreakingKind.TYPE_CHANGED,
                location,
                f"schema list length changed from {len(old_list)} to {len(new_list)}",
            )
        )
        return
    for index, (old_item, new_item) in enumerate(zip(old_list, new_list, strict=True)):
        _compare_schema(old_item, new_item, _join(location, str(index)), findings)


def _compare_enum(
    old_schema: dict[str, Any],
    new_schema: dict[str, Any],
    location: str,
    findings: list[BreakingChange],
) -> None:
    if "enum" not in old_schema and "enum" not in new_schema:
        return
    old_values = _enum_set(old_schema.get("enum"))
    new_values = _enum_set(new_schema.get("enum"))
    here = _join(location, "enum")
    removed = old_values - new_values
    added = new_values - old_values
    if removed:
        findings.append(
            BreakingChange(
                BreakingKind.ENUM_NARROWED,
                here,
                "removed values: " + ", ".join(sorted(removed)),
            )
        )
    if added:
        findings.append(
            BreakingChange(
                BreakingKind.ENUM_WIDENED,
                here,
                "added values: " + ", ".join(sorted(added)),
            )
        )


def _schema_type(schema: dict[str, Any]) -> tuple[str, ...]:
    if "$ref" in schema:
        return ("$ref", str(schema["$ref"]))
    raw = schema.get("type")
    if isinstance(raw, list):
        return tuple(sorted(str(item) for item in raw))
    if isinstance(raw, str):
        return (raw,)
    if "properties" in schema:
        return ("object",)
    if "items" in schema:
        return ("array",)
    return ()


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}


def _enum_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {repr(item) for item in value}
