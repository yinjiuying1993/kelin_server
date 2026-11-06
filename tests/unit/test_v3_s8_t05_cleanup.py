"""V3-S8-T05 expired self-test tickets are deleted. Live tickets stay."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.domain.craft_self_test import GeneratedSkill, StoredTicket
from app.repositories import self_test as self_test_sql
from app.services import cron as cron_service
from app.services.craft_self_test import tickets as ticket_service
from app.services.craft_self_test.tickets import (
    purge_expired_self_test_tickets,
    require_self_test_ticket,
    start_self_test,
)

_OWNER = UUID("00000000-0000-4000-8000-000000000041")
_SPIRIT = UUID("00000000-0000-4000-8000-000000000042")
_CRAFT = UUID("00000000-0000-4000-8000-000000000043")
_NOW = datetime(2026, 11, 4, 5, tzinfo=UTC)
_BODY = "未过期的正文"


class _Store:
    def __init__(self) -> None:
        self.tickets: dict[UUID, StoredTicket] = {}

    async def owner_spirit(self, owner_id: UUID) -> UUID | None:
        del owner_id
        return _SPIRIT

    async def generated_skill(self, craft_id: UUID, spirit_id: UUID) -> GeneratedSkill | None:
        del craft_id, spirit_id
        return GeneratedSkill(skill_slug="pour-tea", body=_BODY)

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


class _Plans:
    job_ids: tuple[object, ...] = ()
    inserted = 0


@pytest.mark.asyncio
async def test_cleanup_keeps_a_ticket_that_has_not_expired() -> None:
    store = _Store()
    fresh = await start_self_test(store, owner_id=_OWNER, craft_id=_CRAFT, now=_NOW)
    due_id = uuid4()
    later_id = uuid4()
    store.tickets[due_id] = StoredTicket(
        ticket_id=due_id,
        spirit_id=_SPIRIT,
        craft_id=_CRAFT,
        skill_slug="pour-tea",
        snapshot_body="刚好到期",
        expires_at=_NOW,
    )
    store.tickets[later_id] = StoredTicket(
        ticket_id=later_id,
        spirit_id=_SPIRIT,
        craft_id=_CRAFT,
        skill_slug="pour-tea",
        snapshot_body="还没到期",
        expires_at=_NOW + timedelta(seconds=1),
    )
    removed = await purge_expired_self_test_tickets(store, now=_NOW)
    assert removed == 1
    assert due_id not in store.tickets
    loaded = await require_self_test_ticket(
        store,
        owner_id=_OWNER,
        ticket_id=fresh.ticket_id,
        craft_id=_CRAFT,
        now=_NOW,
    )
    assert loaded.snapshot_body == _BODY
    assert later_id in store.tickets
    assert "expires_at <= :now" in self_test_sql.DELETE_EXPIRED_SQL


@pytest.mark.asyncio
async def test_worker_tick_counts_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[datetime] = []

    async def _cleanup(factory: object, *, now: datetime) -> int:
        del factory
        seen.append(now)
        return 2

    async def _rows(*args: object, **kwargs: object) -> list[object]:
        del args, kwargs
        return []

    async def _plans(*args: object, **kwargs: object) -> _Plans:
        del args, kwargs
        return _Plans()

    async def _none(*args: object, **kwargs: object) -> int:
        del args, kwargs
        return 0

    monkeypatch.setattr(cron_service, "process_due_state_ticks", _rows)
    monkeypatch.setattr(cron_service, "process_due_visit_plans", _plans)
    monkeypatch.setattr(cron_service, "process_due_visit_settles", _rows)
    monkeypatch.setattr(cron_service, "process_usage_rollup_jobs", _rows)
    monkeypatch.setattr(cron_service, "process_due_account_deletes", _rows)
    monkeypatch.setattr(cron_service, "process_due_extracts", _rows)
    monkeypatch.setattr(cron_service, "process_due_turn_judgments", _rows)
    monkeypatch.setattr(cron_service, "process_stale_memories", _none)
    monkeypatch.setattr(cron_service, "process_due_rating_settlements", _rows)
    monkeypatch.setattr(cron_service, "run_self_test_ticket_cleanup", _cleanup)
    result = await cron_service.run_worker_tick(
        object(),  # type: ignore[arg-type]
        now=_NOW,
        settings=Settings(app_env="test"),
    )
    assert seen == [_NOW]
    assert result.self_test_cleanup == 2
    source = "\n".join(
        (
            inspect.getsource(ticket_service),
            inspect.getsource(cron_service.run_self_test_ticket_cleanup),
        )
    )
    for forbidden in (
        "settle_dialogue",
        "craft_ledgers",
        "help_count",
        "craft_settlements",
        "complete_match",
        "complete_ask",
    ):
        assert forbidden not in source
