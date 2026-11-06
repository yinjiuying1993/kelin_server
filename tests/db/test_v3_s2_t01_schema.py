"""V3-S2-T01 craft tables stay in the inventory and the RLS matrix."""

from __future__ import annotations

import re

from app.db.rls_matrix import (
    RLS_MATRIX_V3,
    OwnerKind,
    enable_force_statements,
    grant_statements,
    policy_blueprints,
    policy_create_statements,
)
from app.models.craft import CraftMatchIndex, CraftSelfTestTicket, CraftSkill
from sqlalchemy import inspect as sa_inspect

from tests.db.schema_inventory import SCHEMA_INVENTORY_V3, v3_inventory_names

_FORBIDDEN_INDEX_COLUMNS = {
    "body",
    "detail",
    "draft_body",
    "draft_detail",
    "reference_links",
    "points",
    "rank_name",
}


def _columns(model: type) -> set[str]:
    return {column.key for column in sa_inspect(model).columns}


def test_v3_inventory_matches_rls_matrix() -> None:
    assert v3_inventory_names() == {row.table for row in RLS_MATRIX_V3}
    assert {
        "craft_skills",
        "craft_match_index",
        "craft_self_test_tickets",
    } <= v3_inventory_names()
    for row in SCHEMA_INVENTORY_V3:
        assert row.spec_section.startswith("v3/")
        assert row.constraints
        assert row.indexes


def test_match_index_has_no_skill_body() -> None:
    present = _columns(CraftMatchIndex)
    assert _FORBIDDEN_INDEX_COLUMNS.isdisjoint(present)
    assert {"active_title", "active_keywords", "staged_title", "staged_keywords"} <= present


def test_skill_rows_keep_a_slug_and_are_not_deleted_by_runtime_roles() -> None:
    assert "skill_slug" in _columns(CraftSkill)
    assert "deleted_at" in _columns(CraftSkill)
    assert "draft_body" in _columns(CraftSkill)
    for statement in grant_statements(RLS_MATRIX_V3):
        if "public.craft_skills" in statement:
            assert "DELETE" not in statement
    assert "snapshot_body" in _columns(CraftSelfTestTicket)
    assert "expires_at" in _columns(CraftSelfTestTicket)


def test_v3_rls_is_forced_and_owner_scoped() -> None:
    tables = {row.table for row in RLS_MATRIX_V3}
    enabled = " ".join(enable_force_statements(RLS_MATRIX_V3))
    for table in tables:
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in enabled
        assert f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY" in enabled
    craft_models = {
        "craft_skills": CraftSkill,
        "craft_match_index": CraftMatchIndex,
        "craft_self_test_tickets": CraftSelfTestTicket,
    }
    for row in RLS_MATRIX_V3:
        assert row.api.delete is False
        if row.table not in craft_models:
            continue
        assert row.owner_kind is OwnerKind.SPIRIT_OWNER
        assert "spirit_id" in _columns(craft_models[row.table])
    policies = " ".join(policy_create_statements(RLS_MATRIX_V3))
    assert "active_title IS NOT NULL" in policies
    assert "TO anon" not in policies
    assert "TO authenticated" not in policies
    names = [item.name for item in policy_blueprints(RLS_MATRIX_V3)]
    assert len(names) == len(set(names))
    for name in names:
        assert len(name) <= 63
        assert re.fullmatch(r"rls_[a-z0-9_]+", name)
