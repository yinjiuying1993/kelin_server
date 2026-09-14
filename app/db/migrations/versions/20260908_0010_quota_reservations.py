"""Quota reservation leases. Spec §§6.4, 15.2.

Revision ID: 20260908_0010
Revises: 20260908_0009
Create Date: 2026-09-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0010"
down_revision: str | None = "20260908_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS private")
    op.execute("REVOKE ALL ON SCHEMA private FROM PUBLIC")
    op.execute(
        "GRANT USAGE ON SCHEMA private TO kelin_api, kelin_worker, kelin_scheduler, kelin_definer"
    )
    op.execute(
        """
        CREATE TABLE private.quota_reservations (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
          capability text NOT NULL,
          usage_date date NOT NULL,
          timezone text NOT NULL,
          source_id uuid NOT NULL,
          amount bigint NOT NULL,
          expires_at timestamptz NOT NULL,
          status text NOT NULL DEFAULT 'held',
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_quota_reservations_capability CHECK (
            capability IN (
              'chat', 'asr', 'tts', 'sight', 'search', 'food',
              'knowledge', 'emotion', 'visit', 'pact'
            )
          ),
          CONSTRAINT ck_quota_reservations_amount CHECK (amount > 0),
          CONSTRAINT ck_quota_reservations_status CHECK (
            status IN ('held', 'expiring', 'committed', 'released')
          ),
          CONSTRAINT uq_quota_reservations_user_capability_source
            UNIQUE (user_id, capability, source_id)
        )
        """
    )
    op.execute("ALTER TABLE private.quota_reservations OWNER TO kelin_migrator")
    op.execute("ALTER TABLE private.quota_reservations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE private.quota_reservations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY rls_quota_reservations_kelin_api_all
        ON private.quota_reservations
        AS PERMISSIVE FOR ALL TO kelin_api
        USING (user_id = auth.uid())
        WITH CHECK (user_id = auth.uid())
        """
    )
    op.execute(
        """
        CREATE POLICY rls_quota_reservations_kelin_worker_all
        ON private.quota_reservations
        AS PERMISSIVE FOR ALL TO kelin_worker
        USING (true)
        WITH CHECK (true)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE private.quota_reservations TO kelin_api")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE private.quota_reservations TO kelin_worker"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS private.quota_reservations")
