"""V3-S2-T04 idempotency registry and dual-account case skeleton.

The live two-account probe stays on V3-S9-T01. This file does not open a
database and does not set a BYPASSRLS role.
"""

from __future__ import annotations

import re

from app.db.rls_matrix import (
    RLS_MATRIX,
    RLS_MATRIX_V3,
    V3_WRITE_IDEMPOTENCY,
    Command,
    grant_statements,
    policy_create_statements,
)
from app.models.ask import AskQuestion, AskRating, AskTurn
from app.models.craft_ledger import CraftSettlement
from app.models.social import IdempotencyRecord

_SPEC_ACTIONS = (
    "创建技能",
    "保存修改",
    "设为私密或再公开",
    "删除技能",
    "提交问题",
    "发送一轮",
    "评分",
    "结算",
)
_MODELS = {
    "idempotency_records": IdempotencyRecord,
    "ask_questions": AskQuestion,
    "ask_turns": AskTurn,
    "ask_ratings": AskRating,
    "craft_settlements": CraftSettlement,
}


def _constraint_names(table: str) -> set[str]:
    return {
        constraint.name
        for constraint in _MODELS[table].__table__.constraints
        if constraint.name
    }


def test_section_11_writes_are_registered_on_real_constraints() -> None:
    assert tuple(row.action for row in V3_WRITE_IDEMPOTENCY) == _SPEC_ACTIONS
    operations = [row.operation for row in V3_WRITE_IDEMPOTENCY]
    assert len(operations) == len(set(operations))
    for row in V3_WRITE_IDEMPOTENCY:
        assert len(row.operation) <= 64
        assert re.fullmatch(r"[a-z0-9.]+", row.operation)
        assert row.constraint in _constraint_names(row.table)
        assert row.live_dual_account == "V3-S9-T01"


def test_each_v3_table_has_a_cross_user_denial() -> None:
    write_commands = {Command.INSERT, Command.UPDATE, Command.DELETE}
    for row in RLS_MATRIX_V3:
        denials = [
            case
            for case in row.test_cases
            if case.actor == "B" and case.command in write_commands and case.expected == "deny"
        ]
        assert denials, row.table
    v3_tables = {row.table for row in RLS_MATRIX_V3}
    assert v3_tables.isdisjoint({row.table for row in RLS_MATRIX})
    assert "idempotency_records" in {row.table for row in RLS_MATRIX}


def test_v3_policies_do_not_use_bypassrls() -> None:
    text = " ".join(policy_create_statements(RLS_MATRIX_V3))
    text = f"{text} {' '.join(grant_statements(RLS_MATRIX_V3))}"
    assert "BYPASSRLS" not in text.upper()
    assert "service_role" not in text
