"""Devices upsert, generic outbox lease, and notification planning. Spec §§8.11, 14.6, 17.1–17.2.

Revision ID: 20260908_0017
Revises: 20260908_0016
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0017"
down_revision: str | None = "20260908_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA auth TO kelin_definer")
    op.execute("GRANT EXECUTE ON FUNCTION auth.uid() TO kelin_definer")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON TABLE public.devices TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE public.user_preferences TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE public.spirits TO {_DEFINER}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON TABLE public.notification_deliveries TO {_DEFINER}"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON TABLE public.outbox_events TO {_DEFINER}")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.upsert_device(
          p_installation_id uuid,
          p_token_hash text,
          p_token_encrypted text,
          p_environment text,
          p_enabled boolean,
          p_app_version text,
          p_locale text,
          p_now timestamptz
        )
        RETURNS TABLE (
          device_id uuid,
          enabled boolean,
          updated_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          uid uuid := auth.uid();
        BEGIN
          IF uid IS NULL OR p_installation_id IS NULL OR p_now IS NULL THEN
            RAISE EXCEPTION 'INVALID_INPUT' USING ERRCODE = '22023';
          END IF;
          IF p_environment IS NULL OR p_environment NOT IN ('sandbox', 'production') THEN
            RAISE EXCEPTION 'INVALID_INPUT' USING ERRCODE = '22023';
          END IF;
          IF p_token_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'INVALID_INPUT' USING ERRCODE = '22023';
          END IF;
          IF p_token_encrypted IS NULL OR btrim(p_token_encrypted) = '' THEN
            RAISE EXCEPTION 'INVALID_INPUT' USING ERRCODE = '22023';
          END IF;
          UPDATE public.devices AS d
          SET
            apns_token_hash = replace(gen_random_uuid()::text, '-', '')
              || substr(replace(d.id::text, '-', ''), 1, 32),
            apns_token_encrypted = 'revoked:' || d.id::text,
            notifications_enabled = false,
            invalidated_at = p_now,
            updated_at = p_now
          WHERE d.environment = p_environment
            AND d.apns_token_hash = p_token_hash
            AND NOT (d.user_id = uid AND d.installation_id = p_installation_id);
          RETURN QUERY
          INSERT INTO public.devices (
            user_id,
            installation_id,
            apns_token_hash,
            apns_token_encrypted,
            environment,
            notifications_enabled,
            app_version,
            locale,
            last_seen_at,
            invalidated_at,
            updated_at
          ) VALUES (
            uid,
            p_installation_id,
            p_token_hash,
            p_token_encrypted,
            p_environment,
            COALESCE(p_enabled, true),
            NULLIF(btrim(p_app_version), ''),
            NULLIF(btrim(p_locale), ''),
            p_now,
            NULL,
            p_now
          )
          ON CONFLICT ON CONSTRAINT uq_devices_user_id_installation_id_environment
          DO UPDATE SET
            apns_token_hash = EXCLUDED.apns_token_hash,
            apns_token_encrypted = EXCLUDED.apns_token_encrypted,
            notifications_enabled = EXCLUDED.notifications_enabled,
            app_version = EXCLUDED.app_version,
            locale = EXCLUDED.locale,
            last_seen_at = p_now,
            invalidated_at = NULL,
            updated_at = p_now
          RETURNING
            public.devices.id,
            public.devices.notifications_enabled,
            public.devices.updated_at;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.upsert_device("
        "uuid, text, text, text, boolean, text, text, timestamptz) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.upsert_device("
        "uuid, text, text, text, boolean, text, text, timestamptz) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.upsert_device("
        "uuid, text, text, text, boolean, text, text, timestamptz) TO kelin_api"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.claim_due_outbox(
          p_now timestamptz,
          p_batch integer,
          p_worker_id text,
          p_lease_seconds integer,
          p_event_types text[]
        )
        RETURNS TABLE (
          job_id uuid,
          event_type text,
          aggregate_id uuid,
          owner_id uuid,
          dedupe_key text,
          payload jsonb,
          locked_by text,
          attempt_count integer
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          batch integer;
          lease integer;
        BEGIN
          IF p_now IS NULL OR p_worker_id IS NULL OR btrim(p_worker_id) = '' THEN
            RAISE EXCEPTION 'claim inputs are required';
          END IF;
          batch := LEAST(GREATEST(COALESCE(p_batch, 0), 1), 50);
          lease := GREATEST(COALESCE(p_lease_seconds, 120), 1);
          RETURN QUERY
          WITH picked AS (
            SELECT o.id
            FROM public.outbox_events o
            WHERE o.status IN ('pending', 'retry')
              AND o.available_at <= p_now
              AND (p_event_types IS NULL OR o.event_type = ANY (p_event_types))
            ORDER BY o.available_at ASC, o.created_at ASC, o.id ASC
            FOR UPDATE OF o SKIP LOCKED
            LIMIT batch
          )
          UPDATE public.outbox_events AS o
          SET
            status = 'claimed',
            locked_by = p_worker_id,
            locked_at = p_now,
            lease_expires_at = p_now + make_interval(secs => lease),
            attempt_count = o.attempt_count + 1
          FROM picked
          WHERE o.id = picked.id
          RETURNING
            o.id,
            o.event_type,
            o.aggregate_id,
            o.owner_id,
            o.dedupe_key,
            o.payload,
            o.locked_by,
            o.attempt_count;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.claim_due_outbox("
        "timestamptz, integer, text, integer, text[]) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.claim_due_outbox("
        "timestamptz, integer, text, integer, text[]) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.claim_due_outbox("
        "timestamptz, integer, text, integer, text[]) TO kelin_worker"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.renew_outbox_lease(
          p_id uuid,
          p_locked_by text,
          p_now timestamptz,
          p_lease_seconds integer
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          lease integer;
          updated integer := 0;
        BEGIN
          IF p_id IS NULL OR p_now IS NULL OR p_locked_by IS NULL OR btrim(p_locked_by) = '' THEN
            RAISE EXCEPTION 'renew inputs are required';
          END IF;
          lease := GREATEST(COALESCE(p_lease_seconds, 120), 1);
          UPDATE public.outbox_events
          SET
            locked_at = p_now,
            lease_expires_at = p_now + make_interval(secs => lease)
          WHERE id = p_id
            AND locked_by = p_locked_by
            AND status = 'claimed'
            AND lease_expires_at IS NOT NULL
            AND lease_expires_at > p_now;
          GET DIAGNOSTICS updated = ROW_COUNT;
          RETURN updated = 1;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.renew_outbox_lease(uuid, text, timestamptz, integer) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.renew_outbox_lease(uuid, text, timestamptz, integer) "
        "FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.renew_outbox_lease(uuid, text, timestamptz, integer) "
        "TO kelin_worker"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.complete_outbox_job(
          p_id uuid,
          p_locked_by text,
          p_now timestamptz
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          updated integer := 0;
        BEGIN
          IF p_id IS NULL OR p_now IS NULL OR p_locked_by IS NULL OR btrim(p_locked_by) = '' THEN
            RAISE EXCEPTION 'complete inputs are required';
          END IF;
          UPDATE public.outbox_events
          SET
            status = 'done',
            processed_at = p_now,
            locked_by = NULL,
            locked_at = NULL,
            lease_expires_at = NULL
          WHERE id = p_id
            AND locked_by = p_locked_by
            AND status = 'claimed'
            AND (lease_expires_at IS NULL OR lease_expires_at > p_now);
          GET DIAGNOSTICS updated = ROW_COUNT;
          RETURN updated = 1;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.complete_outbox_job(uuid, text, timestamptz) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.complete_outbox_job(uuid, text, timestamptz) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.complete_outbox_job(uuid, text, timestamptz) "
        "TO kelin_worker"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.fail_outbox_job(
          p_id uuid,
          p_locked_by text,
          p_now timestamptz,
          p_error text
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          updated integer := 0;
        BEGIN
          IF p_id IS NULL OR p_now IS NULL OR p_locked_by IS NULL OR btrim(p_locked_by) = '' THEN
            RAISE EXCEPTION 'fail inputs are required';
          END IF;
          UPDATE public.outbox_events
          SET
            status = CASE
              WHEN attempt_count >= max_attempts THEN 'dead'
              ELSE 'retry'
            END,
            last_error_code = COALESCE(NULLIF(btrim(p_error), ''), 'HANDLER_FAILED'),
            locked_by = NULL,
            locked_at = NULL,
            lease_expires_at = NULL,
            available_at = CASE
              WHEN attempt_count >= max_attempts THEN available_at
              ELSE p_now + make_interval(
                secs => LEAST(300, POWER(2, GREATEST(attempt_count, 1))::integer)
              )
            END,
            processed_at = CASE
              WHEN attempt_count >= max_attempts THEN p_now
              ELSE processed_at
            END
          WHERE id = p_id
            AND locked_by = p_locked_by
            AND status = 'claimed'
            AND (lease_expires_at IS NULL OR lease_expires_at > p_now);
          GET DIAGNOSTICS updated = ROW_COUNT;
          RETURN updated = 1;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.fail_outbox_job(uuid, text, timestamptz, text) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.fail_outbox_job(uuid, text, timestamptz, text) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.fail_outbox_job(uuid, text, timestamptz, text) "
        "TO kelin_worker"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.release_outbox_until(
          p_id uuid,
          p_locked_by text,
          p_available_at timestamptz,
          p_now timestamptz
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          updated integer := 0;
        BEGIN
          IF p_id IS NULL OR p_locked_by IS NULL OR btrim(p_locked_by) = ''
            OR p_available_at IS NULL OR p_now IS NULL THEN
            RAISE EXCEPTION 'release inputs are required';
          END IF;
          UPDATE public.outbox_events
          SET
            status = 'pending',
            available_at = p_available_at,
            locked_by = NULL,
            locked_at = NULL,
            lease_expires_at = NULL,
            attempt_count = GREATEST(attempt_count - 1, 0)
          WHERE id = p_id
            AND locked_by = p_locked_by
            AND status = 'claimed'
            AND (lease_expires_at IS NULL OR lease_expires_at > p_now);
          GET DIAGNOSTICS updated = ROW_COUNT;
          RETURN updated = 1;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.release_outbox_until(uuid, text, timestamptz, timestamptz) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.release_outbox_until("
        "uuid, text, timestamptz, timestamptz) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.release_outbox_until("
        "uuid, text, timestamptz, timestamptz) TO kelin_worker"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.upsert_notification_delivery(
          p_user_id uuid,
          p_device_id uuid,
          p_event_type text,
          p_resource_id uuid,
          p_dedupe_key text,
          p_scheduled_for timestamptz,
          p_status text,
          p_now timestamptz
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          new_id uuid;
        BEGIN
          IF p_user_id IS NULL OR p_dedupe_key IS NULL OR btrim(p_dedupe_key) = ''
            OR p_event_type IS NULL OR p_scheduled_for IS NULL OR p_now IS NULL THEN
            RAISE EXCEPTION 'delivery inputs are required';
          END IF;
          IF p_status IS NULL OR p_status NOT IN (
            'pending', 'claimed', 'sent', 'suppressed', 'retry', 'dead'
          ) THEN
            RAISE EXCEPTION 'INVALID_INPUT' USING ERRCODE = '22023';
          END IF;
          INSERT INTO public.notification_deliveries (
            user_id,
            device_id,
            event_type,
            resource_id,
            dedupe_key,
            scheduled_for,
            status,
            next_attempt_at,
            updated_at
          ) VALUES (
            p_user_id,
            p_device_id,
            p_event_type,
            p_resource_id,
            p_dedupe_key,
            p_scheduled_for,
            p_status,
            CASE WHEN p_status IN ('pending', 'retry') THEN p_scheduled_for ELSE NULL END,
            p_now
          )
          ON CONFLICT ON CONSTRAINT uq_notification_deliveries_dedupe_key_device_id
          DO UPDATE SET
            scheduled_for = CASE
              WHEN public.notification_deliveries.status IN ('sent', 'dead')
              THEN public.notification_deliveries.scheduled_for
              ELSE EXCLUDED.scheduled_for
            END,
            status = CASE
              WHEN public.notification_deliveries.status IN ('sent', 'dead')
              THEN public.notification_deliveries.status
              ELSE EXCLUDED.status
            END,
            next_attempt_at = CASE
              WHEN public.notification_deliveries.status IN ('sent', 'dead')
              THEN public.notification_deliveries.next_attempt_at
              WHEN EXCLUDED.status IN ('pending', 'retry') THEN EXCLUDED.scheduled_for
              ELSE NULL
            END,
            updated_at = p_now
          RETURNING id INTO new_id;
          RETURN new_id;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.upsert_notification_delivery("
        "uuid, uuid, text, uuid, text, timestamptz, text, timestamptz) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.upsert_notification_delivery("
        "uuid, uuid, text, uuid, text, timestamptz, text, timestamptz) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.upsert_notification_delivery("
        "uuid, uuid, text, uuid, text, timestamptz, text, timestamptz) TO kelin_worker"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.enqueue_due_notification_plan_jobs(p_now timestamptz)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          inserted integer := 0;
        BEGIN
          IF p_now IS NULL THEN
            RAISE EXCEPTION 'now is required';
          END IF;
          INSERT INTO public.outbox_events (
            aggregate_type,
            aggregate_id,
            event_type,
            dedupe_key,
            owner_id,
            payload,
            available_at
          )
          SELECT
            'spirit',
            s.id,
            'notification.plan_user',
            'push:care:' || s.user_id::text || ':'
              || to_char(
                (p_now AT TIME ZONE COALESCE(p.timezone, 'Asia/Shanghai'))::date,
                'YYYY-MM-DD'
              ),
            s.user_id,
            jsonb_build_object('type', 'care', 'resource_id', s.id::text),
            p_now
          FROM public.spirits s
          JOIN public.user_preferences p ON p.user_id = s.user_id
          WHERE p.push_on
            AND s.status <> 'lost'
            AND s.last_interact_at <= p_now - interval '18 hours'
            AND EXISTS (
              SELECT 1
              FROM public.devices d
              WHERE d.user_id = s.user_id
                AND d.notifications_enabled
                AND d.invalidated_at IS NULL
            )
            AND NOT (
              CASE
                WHEN p.dnd_start > p.dnd_end THEN
                  (p_now AT TIME ZONE COALESCE(p.timezone, 'Asia/Shanghai'))::time
                    >= p.dnd_start
                  OR (p_now AT TIME ZONE COALESCE(p.timezone, 'Asia/Shanghai'))::time
                    < p.dnd_end
                ELSE
                  (p_now AT TIME ZONE COALESCE(p.timezone, 'Asia/Shanghai'))::time
                    >= p.dnd_start
                  AND (p_now AT TIME ZONE COALESCE(p.timezone, 'Asia/Shanghai'))::time
                    < p.dnd_end
              END
            )
          ON CONFLICT (dedupe_key) DO NOTHING;
          GET DIAGNOSTICS inserted = ROW_COUNT;
          RETURN inserted;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.enqueue_due_notification_plan_jobs(timestamptz) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.enqueue_due_notification_plan_jobs(timestamptz) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.enqueue_due_notification_plan_jobs(timestamptz) "
        "TO kelin_scheduler"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS private.enqueue_due_notification_plan_jobs(timestamptz)")
    op.execute(
        "DROP FUNCTION IF EXISTS private.upsert_notification_delivery("
        "uuid, uuid, text, uuid, text, timestamptz, text, timestamptz)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS private.release_outbox_until(uuid, text, timestamptz, timestamptz)"
    )
    op.execute("DROP FUNCTION IF EXISTS private.fail_outbox_job(uuid, text, timestamptz, text)")
    op.execute("DROP FUNCTION IF EXISTS private.complete_outbox_job(uuid, text, timestamptz)")
    op.execute(
        "DROP FUNCTION IF EXISTS private.renew_outbox_lease(uuid, text, timestamptz, integer)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS private.claim_due_outbox("
        "timestamptz, integer, text, integer, text[])"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS private.upsert_device("
        "uuid, text, text, text, boolean, text, text, timestamptz)"
    )
    op.execute(f"REVOKE SELECT, INSERT, UPDATE ON TABLE public.devices FROM {_DEFINER}")
    op.execute(
        f"REVOKE SELECT, INSERT, UPDATE ON TABLE public.notification_deliveries FROM {_DEFINER}"
    )
