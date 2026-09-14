"""Worker process entry. Independent of the API process. Spec §17.1–17.2.

Does not send APNs. Notification planning still enforces DND and the daily cap.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_runtime_engine, create_session_factory
from app.services.cron import run_worker_tick

_LOGGER = get_logger(component="worker")


async def _loop(*, once: bool, interval_seconds: int) -> None:
    settings = get_settings()
    configure_logging(settings)
    engine = create_runtime_engine(settings.sqlalchemy_async_url())
    factory = create_session_factory(engine)
    try:
        while True:
            now = datetime.now(UTC)
            result = await run_worker_tick(factory, now=now, settings=settings)
            _LOGGER.info(
                "worker_tick",
                state=result.state,
                visit_plan=result.visit_plan,
                visit_settle=result.visit_settle,
                usage_rollup=result.usage_rollup,
                notification_plan=result.notification_plan,
                report_generate=result.report_generate,
                account_delete=result.account_delete,
            )
            if once:
                return
            await asyncio.sleep(interval_seconds)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kelin outbox worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=5)
    args = parser.parse_args(argv)
    asyncio.run(_loop(once=args.once, interval_seconds=max(args.interval_seconds, 1)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
