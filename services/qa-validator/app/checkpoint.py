import json
import asyncpg

from app.config import get_settings
from app.sql import GET_CHECKPOINT_SQL, UPSERT_CHECKPOINT_SQL


class CheckpointStore:
    """Checkpoint store backed by the existing pipeline_job_progress table.

    The pipeline_job_progress table uses a single ``job_name`` text column
    rather than a (pipeline_name, run_id) pair. We compose a stable job_name
    of the form ``"{pipeline_name}:{run_id}"`` so each QA run has its own
    checkpoint row without requiring a schema change to pipeline_job_progress.
    """

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn
        self.settings = get_settings()

    def _job_name(self, run_id: int) -> str:
        return f"{self.settings.pipeline_name}:{run_id}"

    async def get_last_processed_id(self, run_id: int) -> int:
        row = await self.conn.fetchrow(
            GET_CHECKPOINT_SQL,
            self._job_name(run_id),
        )
        return int(row["last_processed_id"]) if row and row["last_processed_id"] is not None else 0

    async def save_last_processed_id(self, run_id: int, last_processed_id: int, meta: dict) -> None:
        await self.conn.execute(
            UPSERT_CHECKPOINT_SQL,
            self._job_name(run_id),
            last_processed_id,
            json.dumps(meta),
        )
