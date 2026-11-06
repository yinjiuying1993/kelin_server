"""V3-S5-T01 only the owner receives a short-lived snapshot ticket."""

from __future__ import annotations

import inspect
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.core.errors import ApiError
from app.db.rls_matrix import RLS_MATRIX_V3, grant_statements, policy_blueprints
from app.domain.craft_self_test import (
    SELF_TEST_TICKET_TTL,
    GeneratedSkill,
    IssuedTicket,
    StoredTicket,
)
from app.repositories import self_test as self_test_sql
from app.services.craft_self_test import tickets as ticket_service
from app.services.craft_self_test.tickets import (
    purge_expired_self_test_tickets,
    require_self_test_ticket,
    start_self_test,
)

_OWNER = UUID("00000000-0000-4000-8000-000000000031")
_OTHER = UUID("00000000-0000-4000-8000-000000000032")
_SPIRIT = UUID("00000000-0000-4000-8000-000000000033")
_OTHER_SPIRIT = UUID("00000000-0000-4000-8000-000000000034")
_CRAFT = UUID("00000000-0000-4000-8000-000000000035")
_OTHER_CRAFT = UUID("00000000-0000-4000-8000-000000000036")
_NOW = datetime(2026, 11, 4, 2, 0, tzinfo=UTC)
_BODY = "进入时的正文"


class _Store:
    def __init__(self) -> None:
        self.spirits = {_OWNER: _SPIRIT, _OTHER: _OTHER_SPIRIT}
        self.skills = {
            _CRAFT: GeneratedSkill(skill_slug="pour-tea", body=_BODY),
            _OTHER_CRAFT: GeneratedSkill(skill_slug="other-tea", body="另一门正文"),
        }
        self.skill_spirit = {_CRAFT: _SPIRIT, _OTHER_CRAFT: _OTHER_SPIRIT}
        self.tickets: dict[UUID, StoredTicket] = {}

    async def owner_spirit(self, owner_id: UUID) -> UUID | None:
        return self.spirits.get(owner_id)

    async def generated_skill(self, craft_id: UUID, spirit_id: UUID) -> GeneratedSkill | None:
        if self.skill_spirit.get(craft_id) != spirit_id:
            return None
        return self.skills.get(craft_id)

    async def insert_ticket(
        self,
        *,
        spirit_id: UUID,
        craft_id: UUID,
        skill_slug: str,
        snapshot_body: str,
        expires_at: datetime,
    ) -> UUID:
        ticket_id = uuid4()
        self.tickets[ticket_id] = StoredTicket(
            ticket_id=ticket_id,
            spirit_id=spirit_id,
            craft_id=craft_id,
            skill_slug=skill_slug,
            snapshot_body=snapshot_body,
            expires_at=expires_at,
        )
        return ticket_id

    async def get_ticket(self, ticket_id: UUID) -> StoredTicket | None:
        return self.tickets.get(ticket_id)

    async def delete_expired(self, now: datetime) -> int:
        expired = [
            ticket_id
            for ticket_id, ticket in self.tickets.items()
            if ticket.expires_at <= now
        ]
        for ticket_id in expired:
            del self.tickets[ticket_id]
        return len(expired)


@pytest.mark.asyncio
async def test_owner_ticket_keeps_the_body_from_the_start() -> None:
    store = _Store()
    issued = await start_self_test(store, owner_id=_OWNER, craft_id=_CRAFT, now=_NOW)
    assert {field.name for field in fields(IssuedTicket)} == {"ticket_id", "expires_at"}
    assert issued.expires_at == _NOW + SELF_TEST_TICKET_TTL
    assert "pour-tea" not in str(issued)
    store.skills[_CRAFT] = GeneratedSkill(skill_slug="pour-tea", body="保存后的新正文")
    loaded = await require_self_test_ticket(
        store,
        owner_id=_OWNER,
        ticket_id=issued.ticket_id,
        craft_id=_CRAFT,
        now=_NOW + timedelta(minutes=29),
    )
    assert loaded.snapshot_body == _BODY
    assert loaded.craft_id == _CRAFT


@pytest.mark.asyncio
async def test_ticket_rejects_another_skill_and_an_expired_one() -> None:
    store = _Store()
    issued = await start_self_test(store, owner_id=_OWNER, craft_id=_CRAFT, now=_NOW)
    with pytest.raises(ApiError) as other_skill:
        await require_self_test_ticket(
            store,
            owner_id=_OWNER,
            ticket_id=issued.ticket_id,
            craft_id=_OTHER_CRAFT,
            now=_NOW,
        )
    assert other_skill.value.code == "NOT_FOUND"
    assert "other-tea" not in str(other_skill.value)
    with pytest.raises(ApiError) as other_owner:
        await start_self_test(store, owner_id=_OTHER, craft_id=_CRAFT, now=_NOW)
    assert other_owner.value.code == "NOT_FOUND"
    with pytest.raises(ApiError) as expired:
        await require_self_test_ticket(
            store,
            owner_id=_OWNER,
            ticket_id=issued.ticket_id,
            craft_id=_CRAFT,
            now=issued.expires_at,
        )
    assert expired.value.code == "NOT_FOUND"
    assert store.tickets[issued.ticket_id].snapshot_body == _BODY


@pytest.mark.asyncio
async def test_cleanup_removes_only_expired_tickets() -> None:
    store = _Store()
    fresh = await start_self_test(store, owner_id=_OWNER, craft_id=_CRAFT, now=_NOW)
    stale_id = uuid4()
    store.tickets[stale_id] = replace(
        store.tickets[fresh.ticket_id],
        ticket_id=stale_id,
        expires_at=_NOW - timedelta(seconds=1),
    )
    removed = await purge_expired_self_test_tickets(store, now=_NOW)
    assert removed == 1
    assert stale_id not in store.tickets
    loaded = await require_self_test_ticket(
        store,
        owner_id=_OWNER,
        ticket_id=fresh.ticket_id,
        craft_id=_CRAFT,
        now=_NOW,
    )
    assert loaded.snapshot_body == _BODY


def test_cleanup_entry_does_not_write_ask_or_help_counts() -> None:
    source = "\n".join(
        (
            inspect.getsource(self_test_sql),
            inspect.getsource(ticket_service),
        )
    )
    for forbidden in ("ask_questions", "craft_ledgers", "help_count", "bailian"):
        assert forbidden not in source
    assert "kelin_worker" in inspect.getsource(ticket_service.run_self_test_ticket_cleanup)
    worker_grants = [
        statement
        for statement in grant_statements(RLS_MATRIX_V3)
        if "craft_self_test_tickets" in statement and "kelin_worker" in statement
    ]
    assert worker_grants == ["GRANT DELETE ON TABLE public.craft_self_test_tickets TO kelin_worker"]
    worker_policies = [
        policy
        for policy in policy_blueprints(RLS_MATRIX_V3)
        if policy.table == "craft_self_test_tickets" and "kelin_worker" in policy.roles
    ]
    assert [(policy.command, policy.using_sql) for policy in worker_policies] == [
        ("DELETE", "true")
    ]
    api_delete = [
        statement
        for statement in grant_statements(RLS_MATRIX_V3)
        if "craft_self_test_tickets" in statement and "kelin_api" in statement
    ]
    assert all("DELETE" not in statement for statement in api_delete)
