import logging
import subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.api.qa import router as qa_router
from app.config import get_settings
from app.db import close_db, init_db
from app.logging_config import configure_logging
from app.scheduler import build_scheduler

scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        sha = "unknown"

    logging.getLogger(__name__).info(
        f"BOOT qa-validator SHA={sha} env={settings.environment}"
    )

    await init_db()

    global scheduler
    scheduler = build_scheduler()
    if scheduler:
        scheduler.start()

    yield

    if scheduler:
        scheduler.shutdown(wait=False)
    await close_db()


app = FastAPI(
    title="QA Validation Service",
    version="1.0.0",
    lifespan=lifespan,
)


app.include_router(qa_router)
