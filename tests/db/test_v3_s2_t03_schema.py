"""V3-S2-T03 ledger and settlement stay off the growth tables."""

from __future__ import annotations

from app.db.rls_matrix import RLS_MATRIX_V3, grant_statements
from app.models.ask import AskQuestion
from app.models.craft_ledger import CraftLedger, CraftSettlement
from app.models.identity import Spirit
from sqlalchemy import UniqueConstraint

from tests.db.schema_inventory import v3_inventory_names

_GROWTH_TABLES = {"feeds", "growth_events", "spirit_emotion", "spirit_growth"}


def test_ledger_and_settlement_are_registered() -> None:
    assert AskQuestion.__tablename__ == "ask_questions"
    assert Spirit.__tablename__ == "spirits"
    assert {"craft_ledgers", "craft_settlements"} <= v3_inventory_names()
    assert {"craft_ledgers", "craft_settlements"} <= {row.table for row in RLS_MATRIX_V3}


def test_settlement_is_unique_per_dialogue() -> None:
    uniques = [
        constraint
        for constraint in CraftSettlement.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert any(tuple(item.columns.keys()) == ("ask_question_id",) for item in uniques)
    columns = {column.key for column in CraftSettlement.__table__.columns}
    assert "question_text" not in columns
    assert "help_counted" in columns
    assert "points_added" in columns


def test_ledger_does_not_reference_growth() -> None:
    for model in (CraftLedger, CraftSettlement):
        for foreign_key in model.__table__.foreign_keys:
            assert foreign_key.column.table.name not in _GROWTH_TABLES
    targets = {fk.column.table.name for fk in CraftLedger.__table__.foreign_keys}
    assert targets == {"spirits"}
    settlement_targets = {fk.column.table.name for fk in CraftSettlement.__table__.foreign_keys}
    assert settlement_targets == {"ask_questions"}


def test_api_cannot_write_points_or_redeem() -> None:
    rows = {row.table: row for row in RLS_MATRIX_V3}
    ledger = rows["craft_ledgers"]
    assert ledger.api.select is True
    assert ledger.api.insert is False
    assert ledger.api.update is False
    assert ledger.worker.update_columns == ("total_points", "help_count", "updated_at")
    assert "redeemed_at" not in ledger.worker.update_columns
    assert rows["craft_settlements"].api.select is False
    assert rows["craft_settlements"].worker.update is False
    grants = grant_statements(
        tuple(row for row in rows.values() if row.table in {"craft_ledgers", "craft_settlements"})
    )
    joined = " ".join(grants)
    assert "GRANT UPDATE (total_points, help_count, updated_at)" in joined
    assert "redeemed_at" not in joined
    for statement in grants:
        assert "DELETE" not in statement
        assert "growth" not in statement
