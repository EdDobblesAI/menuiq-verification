from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import asyncpg

from app.sql import UPSERT_RESULTS_UNNEST_SQL, BULK_COMPLETE_RUN_ITEMS_SQL


@dataclass
class ResultRow:
    venue_id: int
    sample_run_id: int
    freshness_score: float
    venue_accuracy_score: float
    menu_validity_score: float
    beverage_relevance_score: float
    composite_score: float
    failure_reason: str
    validated_at: datetime
    live_fetch_status: int | None
    live_url: str | None
    live_domain: str | None
    llm_used: bool
    debug_meta: dict


@dataclass
class ItemCompletionRow:
    run_id: int
    seq_id: int
    item_status: str
    processed_at: datetime


class BatchWriter:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def flush_results(self, rows: list[ResultRow]) -> None:
        if not rows:
            return

        await self.conn.execute(
            UPSERT_RESULTS_UNNEST_SQL,
            [r.venue_id for r in rows],
            [r.sample_run_id for r in rows],
            [r.freshness_score for r in rows],
            [r.venue_accuracy_score for r in rows],
            [r.menu_validity_score for r in rows],
            [r.beverage_relevance_score for r in rows],
            [r.composite_score for r in rows],
            [r.failure_reason for r in rows],
            [r.validated_at for r in rows],
            [r.live_fetch_status for r in rows],
            [r.live_url for r in rows],
            [r.live_domain for r in rows],
            [r.llm_used for r in rows],
            [json.dumps(r.debug_meta) for r in rows],
        )

    async def flush_item_completions(self, rows: list[ItemCompletionRow]) -> None:
        if not rows:
            return

        await self.conn.execute(
            BULK_COMPLETE_RUN_ITEMS_SQL,
            [r.run_id for r in rows],
            [r.seq_id for r in rows],
            [r.item_status for r in rows],
            [r.processed_at for r in rows],
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
