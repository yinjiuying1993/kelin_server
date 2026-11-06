"""Stop the test spirit's automatic departure. The console sets away.

Revision ID: 20261021_0110
Revises: 20261021_0109
Create Date: 2026-10-21
"""

from collections.abc import Sequence
import importlib.util
from pathlib import Path

from alembic import op

revision: str = "20261021_0110"
down_revision: str | None = "20261021_0109"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEST_CODE = "DP4RBKLV"


def _load(name: str) -> object:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"migration source is missing: {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    previous = _load("20261021_0109_test_visit_clock.py")
    previous.downgrade()
    op.execute(
        f"""
        UPDATE public.outbox_events AS event
        SET status = 'done', processed_at = clock_timestamp()
        FROM public.spirits AS spirit
        WHERE event.owner_id = spirit.user_id
          AND spirit.invite_code = '{_TEST_CODE}'
          AND event.status IN ('pending', 'retry')
          AND event.event_type IN ('state.settle', 'visit.plan')
          AND event.dedupe_key ~ ':[0-9]{{12}}$'
        """
    )
    op.execute(
        f"""
        UPDATE public.outbox_events AS event
        SET status = 'done', processed_at = clock_timestamp()
        WHERE event.status IN ('pending', 'retry')
          AND event.event_type = 'visit.settle'
          AND event.dedupe_key IN (
            SELECT 'visit-settle:' || visit.id::text
            FROM public.visits AS visit
            JOIN public.spirits AS spirit
              ON spirit.id = visit.visitor_spirit_id
            WHERE spirit.invite_code = '{_TEST_CODE}'
              AND visit.status IN ('eligible', 'visiting')
              AND visit.eligibility_key ~ ':[0-9]{{12}}$'
          )
        """
    )
    op.execute(
        f"""
        UPDATE public.visits AS visit
        SET status = 'settled',
            settled_at = clock_timestamp(),
            updated_at = clock_timestamp()
        FROM public.spirits AS spirit
        WHERE visit.visitor_spirit_id = spirit.id
          AND spirit.invite_code = '{_TEST_CODE}'
          AND visit.status IN ('eligible', 'visiting')
          AND visit.eligibility_key ~ ':[0-9]{{12}}$'
        """
    )


def downgrade() -> None:
    previous = _load("20261021_0109_test_visit_clock.py")
    previous.upgrade()
