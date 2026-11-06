"""V3-S2-T02 ask dialogue, turns, and one rating stay with the asker."""

from __future__ import annotations

import re

from app.db.rls_matrix import (
    RLS_MATRIX_V3,
    OwnerKind,
    grant_statements,
    policy_blueprints,
)
from app.models.ask import AskQuestion, AskRating, AskTurn
from sqlalchemy import UniqueConstraint

from tests.db.schema_inventory import v3_inventory_names

_SNAPSHOT = {
    "snapshot_title",
    "snapshot_rank_name",
    "snapshot_skill_slug",
    "snapshot_body",
    "snapshot_detail",
    "snapshot_reference_links",
}


def _constraint_names(model: type) -> set[str]:
    return {constraint.name for constraint in model.__table__.constraints if constraint.name}


def test_ask_tables_are_in_the_v3_inventory() -> None:
    assert {
        "ask_questions",
        "ask_turns",
        "ask_ratings",
    } <= v3_inventory_names()
    tables = {row.table for row in RLS_MATRIX_V3}
    assert {"ask_questions", "ask_turns", "ask_ratings"} <= tables


def test_submit_turn_and_rating_uniques_cover_section_11() -> None:
    assert "uq_ask_questions_asker_client" in _constraint_names(AskQuestion)
    assert "uq_ask_turns_question_client" in _constraint_names(AskTurn)
    assert "uq_ask_ratings_question" in _constraint_names(AskRating)
    rating_uniques = [
        constraint
        for constraint in AskRating.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert any(
        tuple(constraint.columns.keys()) == ("ask_question_id",) for constraint in rating_uniques
    )


def test_snapshot_is_copied_and_not_tied_to_the_skill_row() -> None:
    columns = {column.key for column in AskQuestion.__table__.columns}
    assert _SNAPSHOT <= columns
    assert "first_reply_at" in columns
    for model in (AskQuestion, AskTurn, AskRating):
        for foreign_key in model.__table__.foreign_keys:
            assert foreign_key.column.table.name != "craft_skills"
            assert foreign_key.onupdate != "CASCADE"


def test_rls_owner_is_the_asker_and_snapshot_is_not_updated() -> None:
    rows = {row.table: row for row in RLS_MATRIX_V3}
    question = rows["ask_questions"]
    assert question.owner_kind is OwnerKind.USER_COLUMN
    assert question.owner_sql == "asker_user_id = auth.uid()"
    assert question.api.update_columns == ("first_reply_at",)
    assert question.api.delete is False
    assert question.worker.select is False
    assert rows["ask_turns"].api.update_columns == ("reply_text", "status")
    assert rows["ask_ratings"].api.update is False
    ask_rows = tuple(row for row in rows.values() if row.table.startswith("ask_"))
    grants = grant_statements(ask_rows)
    assert "GRANT UPDATE (first_reply_at) ON TABLE public.ask_questions TO kelin_api" in grants
    for statement in grants:
        assert "DELETE" not in statement
        assert "snapshot_" not in statement
    names = [item.name for item in policy_blueprints(ask_rows)]
    assert names
    assert len(names) == len(set(names))
    for name in names:
        assert len(name) <= 63
        assert re.fullmatch(r"rls_[a-z0-9_]+", name)
        assert "kelin_worker" not in name
