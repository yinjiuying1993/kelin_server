"""P17-T03 planner enforces DND, daily cap, push_off, and care rules."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.schemas.devices import RegisterDeviceRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.devices import register_device
from app.services.notification_planner import (
    process_due_notification_plans,
    scan_due_notification_plan_jobs,
)
from app.services.spirit import create_spirit_if_absent
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 11, 0, tzinfo=UTC)
DND_NOW = datetime(2026, 9, 12, 16, 0, tzinfo=UTC)


def _spirit(name: str) -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "egg": "warm",
            "name": name,
            "consents": {
                "ai_disclosure": {"document_version": "2026-09", "explicitly_accepted": True},
                "data_notice": {"document_version": "2026-09", "displayed": True},
                "user_terms": {"document_version": "2026-09", "displayed": True},
            },
        }
    )


async def _insert_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _boot(url: str, factory, user_id: uuid.UUID, name: str) -> CurrentUser:
    await _insert_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        await create_spirit_if_absent(session, user, _spirit(name))
    return user


async def _enable_push_and_device(factory, user: CurrentUser, settings: Settings) -> None:
    token = uuid.uuid4().hex + uuid.uuid4().hex
    async with claimed_transaction(factory, user) as session:
        await session.execute(
            text("UPDATE public.user_preferences SET push_on = true WHERE user_id = :id"),
            {"id": user.id},
        )
        await register_device(
            session,
            user,
            RegisterDeviceRequest.model_validate(
                {
                    "client_id": str(uuid.uuid4()),
                    "installation_id": str(uuid.uuid4()),
                    "apns_token": token,
                    "environment": "sandbox",
                    "enabled": True,
                }
            ),
            now=NOW,
            settings=settings,
        )


async def _insert_plan(
    url: str,
    *,
    owner_id: uuid.UUID,
    event_type: str,
    resource_id: uuid.UUID,
    available_at: datetime,
) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.outbox_events ("
                "aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload, "
                "available_at"
                ") VALUES ("
                "'spirit', :resource_id, 'notification.plan_user', :dedupe, :owner_id, "
                "CAST(:payload AS jsonb), :now"
                ")"
            ),
            {
                "resource_id": resource_id,
                "dedupe": (
                    f"push:{event_type}:{resource_id}:{owner_id}:"
                    f"{available_at.date().isoformat()}-job"
                ),
                "owner_id": owner_id,
                "payload": json.dumps({"type": event_type, "resource_id": str(resource_id)}),
                "now": available_at,
            },
        )
    await engine.dispose()


def test_notification_plan_dnd_cap_care_and_privacy() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_plans(url))


async def _assert_plans(url: str) -> None:
    settings = Settings(app_env="test", database_url_api=SecretStr(url))
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    postcard_user = uuid.uuid4()
    off_user = uuid.uuid4()
    cap_user = uuid.uuid4()
    care_user = uuid.uuid4()
    promise_user = uuid.uuid4()
    owner_postcard = await _boot(url, factory, postcard_user, "明信")
    await _boot(url, factory, off_user, "关闭")
    owner_cap = await _boot(url, factory, cap_user, "限额")
    owner_care = await _boot(url, factory, care_user, "关心")
    owner_promise = await _boot(url, factory, promise_user, "约定")
    await _enable_push_and_device(factory, owner_postcard, settings)
    await _enable_push_and_device(factory, owner_cap, settings)
    await _enable_push_and_device(factory, owner_care, settings)
    await _enable_push_and_device(factory, owner_promise, settings)
    postcard_id = uuid.uuid4()
    await _insert_plan(
        url,
        owner_id=postcard_user,
        event_type="postcard",
        resource_id=postcard_id,
        available_at=DND_NOW,
    )
    await _insert_plan(
        url,
        owner_id=off_user,
        event_type="postcard",
        resource_id=uuid.uuid4(),
        available_at=NOW,
    )
    first_cap = uuid.uuid4()
    second_cap = uuid.uuid4()
    third_cap = uuid.uuid4()
    await _insert_plan(
        url, owner_id=cap_user, event_type="pact", resource_id=first_cap, available_at=NOW
    )
    await _insert_plan(
        url, owner_id=cap_user, event_type="lost", resource_id=second_cap, available_at=NOW
    )
    await _insert_plan(
        url, owner_id=cap_user, event_type="postcard", resource_id=third_cap, available_at=NOW
    )
    await _insert_plan(
        url,
        owner_id=promise_user,
        event_type="promise",
        resource_id=uuid.uuid4(),
        available_at=NOW,
    )
    async with claimed_transaction(factory, owner_care) as session:
        await session.execute(
            text(
                "UPDATE public.spirits SET last_interact_at = :ts WHERE user_id = :id"
            ),
            {"ts": NOW - timedelta(hours=18), "id": care_user},
        )
    first = await process_due_notification_plans(factory, now=NOW, settings=settings)
    dnd = await process_due_notification_plans(factory, now=DND_NOW, settings=settings)
    by_owner = {item.owner_id: item for item in (*first, *dnd)}
    assert by_owner[postcard_user].status == "pending"
    assert by_owner[off_user].status == "suppressed"
    assert by_owner[promise_user].status == "suppressed"
    cap_items = [item for item in first if item.owner_id == cap_user]
    cap_statuses = sorted(item.status for item in cap_items)
    assert cap_statuses.count("pending") == 2
    assert cap_statuses.count("suppressed") == 1
    inspect = create_async_engine(url)
    async with inspect.connect() as conn:
        dnd_row = (
            await conn.execute(
                text(
                    "SELECT status, scheduled_for, event_type, resource_id "
                    "FROM public.notification_deliveries WHERE user_id = :id"
                ),
                {"id": postcard_user},
            )
        ).mappings().one()
        payload_keys = (
            await conn.execute(
                text(
                    "SELECT payload FROM public.outbox_events "
                    "WHERE owner_id = :id AND event_type = 'notification.plan_user' "
                    "LIMIT 1"
                ),
                {"id": postcard_user},
            )
        ).scalar_one()
    await inspect.dispose()
    assert dnd_row["status"] == "pending"
    assert dnd_row["scheduled_for"] == datetime(2026, 9, 13, 0, 0, tzinfo=UTC)
    assert set(payload_keys) <= {"type", "resource_id"}
    inserted = await scan_due_notification_plan_jobs(factory, now=NOW)
    again = await scan_due_notification_plan_jobs(factory, now=NOW)
    assert inserted >= 1
    assert again == 0
    care_result = await process_due_notification_plans(factory, now=NOW, settings=settings)
    care_items = [item for item in care_result if item.owner_id == care_user]
    assert care_items
    assert care_items[0].event_type == "care"
    assert care_items[0].status == "pending"
    lost_user = uuid.uuid4()
    owner_lost = await _boot(url, factory, lost_user, "走失")
    await _enable_push_and_device(factory, owner_lost, settings)
    async with claimed_transaction(factory, owner_lost) as session:
        await session.execute(
            text(
                "UPDATE public.spirits SET status = 'lost', last_interact_at = :ts "
                "WHERE user_id = :id"
            ),
            {"ts": NOW - timedelta(hours=20), "id": lost_user},
        )
    lost_inserted = await scan_due_notification_plan_jobs(factory, now=NOW)
    assert lost_inserted == 0
    await engine.dispose()
