import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db import get_pool
from app.orchestrator import RunOrchestrator
from app.sampling import materialize_run_items
from app.sql import ACTIVE_RUN_SQL, CREATE_SAMPLE_RUN_SQL

logger = logging.getLogger(__name__)


async def scheduled_daily_run() -> None:
    settings = get_settings()
    pool = await get_pool()
    orchestrator = RunOrchestrator()

    async with pool.acquire() as conn:
        active = await conn.fetchrow(ACTIVE_RUN_SQL)
        if active:
            logger.warning("scheduled_run_skipped active_run_id=%s", active["id"])
            return

        run_id = await conn.fetchval(
            CREATE_SAMPLE_RUN_SQL,
            "sample",
            settings.default_sample_size,
            None,
            "scheduler",
        )
        actual_size = await materialize_run_items(
            conn,
            run_id=run_id,
            run_type="sample",
            sample_size=settings.default_sample_size,
            state_filter=None,
        )
        await conn.execute(
            "UPDATE qa.qa_sample_runs SET sample_size = $2 WHERE id = $1;",
            run_id,
            actual_size,
        )

    await orchestrator.run(run_id)
    await orchestrator.close()


def build_scheduler() -> AsyncIOScheduler | None:
    settings = get_settings()
    if not settings.scheduler_enabled:
        return None

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        scheduled_daily_run,
        CronTrigger(hour=settings.scheduler_cron_hour_utc, minute=settings.scheduler_cron_minute_utc),
        id="qa_daily_run",
        max_instances=1,
        replace_existing=True,
        coalesce=True,
    )
    return scheduler
