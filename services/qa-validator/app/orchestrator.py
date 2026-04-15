from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.checkpoint import CheckpointStore
from app.config import get_settings
from app.db import get_pool
from app.fetcher import LiveFetcher
from app.llm import HaikuClassifier
from app.normalizer import html_to_text
from app.scoring import (
    ScoreBundle,
    choose_failure_reason,
    compute_composite,
    needs_llm,
    score_beverage_relevance,
    score_freshness,
    score_menu_validity_rule,
    score_venue_accuracy,
)
from app.sql import (
    GET_PENDING_ITEMS_SQL,
    GET_RUN_SQL,
    LATEST_CAPTURE_SQL,
    MARK_RUN_COMPLETED_SQL,
    MARK_RUN_FAILED_SQL,
    MARK_RUN_RUNNING_SQL,
    PASS_RATE_SO_FAR_SQL,
    RUN_STATUS_COUNTS_SQL,
)
from app.writer import BatchWriter, ItemCompletionRow, ResultRow, utcnow
from app.reports import generate_daily_report, upsert_daily_report

logger = logging.getLogger(__name__)


@dataclass
class RunItem:
    run_id: int
    seq_id: int
    venue_id: int
    state: str | None
    website_url: str
    domain: str | None


class RunOrchestrator:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.fetcher = LiveFetcher()
        self.haiku = HaikuClassifier()
        self._in_process_runs: set[int] = set()

    async def close(self) -> None:
        await self.fetcher.close()

    async def acquire_advisory_lock(self, conn) -> bool:
        row = await conn.fetchrow("SELECT pg_try_advisory_lock($1) AS ok;", self.settings.advisory_lock_key)
        return bool(row["ok"])

    async def release_advisory_lock(self, conn) -> None:
        await conn.execute("SELECT pg_advisory_unlock($1);", self.settings.advisory_lock_key)

    async def run(self, run_id: int) -> None:
        if run_id in self._in_process_runs:
            logger.info("run_already_in_process run_id=%s", run_id)
            return

        pool = await get_pool()
        self._in_process_runs.add(run_id)

        try:
            async with pool.acquire() as conn:
                locked = await self.acquire_advisory_lock(conn)
                if not locked:
                    logger.warning("advisory_lock_unavailable run_id=%s", run_id)
                    return

                run = await conn.fetchrow(GET_RUN_SQL, run_id)
                if not run:
                    raise RuntimeError(f"Run not found: {run_id}")

                await conn.execute(MARK_RUN_RUNNING_SQL, run_id)

                checkpoint = CheckpointStore(conn)
                last_processed = await checkpoint.get_last_processed_id(run_id)

                logger.info("run_start run_id=%s last_processed=%s", run_id, last_processed)

                await self._process_run(conn, run_id, last_processed)

                pass_rate_row = await conn.fetchrow(PASS_RATE_SO_FAR_SQL, run_id)
                mean_row = await conn.fetchrow(
                    "SELECT AVG(composite_score) AS mean_score FROM qa.qa_results WHERE sample_run_id = $1;",
                    run_id,
                )
                pass_rate = float(pass_rate_row["pass_rate"]) if pass_rate_row and pass_rate_row["pass_rate"] is not None else 0.0
                mean_score = float(mean_row["mean_score"]) if mean_row and mean_row["mean_score"] is not None else 0.0

                await conn.execute(MARK_RUN_COMPLETED_SQL, run_id, pass_rate, mean_score)

                report_date = run["started_at"].date() if run["started_at"] else utcnow().date()
                report_json = await generate_daily_report(conn, report_date)
                if report_json:
                    await upsert_daily_report(conn, report_date, run_id, report_json)

                await self.release_advisory_lock(conn)

        except Exception:
            logger.exception("run_failed run_id=%s", run_id)
            async with pool.acquire() as conn:
                await conn.execute(MARK_RUN_FAILED_SQL, run_id)
            raise
        finally:
            self._in_process_runs.discard(run_id)

    async def _process_run(self, conn, run_id: int, last_processed: int) -> None:
        writer = BatchWriter(conn)
        checkpoint = CheckpointStore(conn)
        semaphore = asyncio.Semaphore(self.settings.max_concurrency)

        result_buffer: list[ResultRow] = []
        item_buffer: list[ItemCompletionRow] = []
        max_completed_seq = last_processed

        while True:
            rows = await conn.fetch(GET_PENDING_ITEMS_SQL, run_id, last_processed, self.settings.batch_flush_size * 3)
            if not rows:
                break

            items = [RunItem(**dict(r)) for r in rows]

            async def bounded(item: RunItem):
                async with semaphore:
                    return await self._validate_item(conn, item)

            tasks = [asyncio.create_task(bounded(item)) for item in items]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for item, outcome in zip(items, results):
                if isinstance(outcome, Exception):
                    logger.exception("item_failed run_id=%s seq_id=%s venue_id=%s", item.run_id, item.seq_id, item.venue_id)
                    bundle = ScoreBundle(
                        freshness_score=0.0,
                        venue_accuracy_score=0.0,
                        menu_validity_score=0.0,
                        beverage_relevance_score=0.0,
                        composite_score=0.0,
                        failure_reason="fetch_failed",
                        llm_used=False,
                        debug_meta={"exception": str(outcome)},
                    )
                    fetch_status = None
                    final_url = item.website_url
                    live_domain = item.domain
                else:
                    bundle, fetch_status, final_url, live_domain = outcome

                result_buffer.append(
                    ResultRow(
                        venue_id=item.venue_id,
                        sample_run_id=item.run_id,
                        freshness_score=bundle.freshness_score,
                        venue_accuracy_score=bundle.venue_accuracy_score,
                        menu_validity_score=bundle.menu_validity_score,
                        beverage_relevance_score=bundle.beverage_relevance_score,
                        composite_score=bundle.composite_score,
                        failure_reason=bundle.failure_reason,
                        validated_at=utcnow(),
                        live_fetch_status=fetch_status,
                        live_url=final_url,
                        live_domain=live_domain,
                        llm_used=bundle.llm_used,
                        debug_meta=bundle.debug_meta,
                    )
                )
                item_buffer.append(
                    ItemCompletionRow(
                        run_id=item.run_id,
                        seq_id=item.seq_id,
                        item_status="completed",
                        processed_at=utcnow(),
                    )
                )
                max_completed_seq = max(max_completed_seq, item.seq_id)

            await writer.flush_results(result_buffer)
            await writer.flush_item_completions(item_buffer)
            await checkpoint.save_last_processed_id(
                run_id,
                max_completed_seq,
                {"batch_size": len(item_buffer), "status": "running"},
            )

            result_buffer.clear()
            item_buffer.clear()
            last_processed = max_completed_seq

    async def _validate_item(self, conn, item: RunItem) -> tuple[ScoreBundle, int | None, str | None, str | None]:
        src = await conn.fetchrow(LATEST_CAPTURE_SQL, item.venue_id)
        if not src:
            bundle = ScoreBundle(
                freshness_score=0.0,
                venue_accuracy_score=0.0,
                menu_validity_score=0.0,
                beverage_relevance_score=0.0,
                composite_score=0.0,
                failure_reason="fetch_failed",
                debug_meta={"source_missing": True},
            )
            return bundle, None, item.website_url, item.domain

        fetch = await self.fetcher.fetch(src["website_url"])
        if fetch.error or not fetch.text:
            bundle = ScoreBundle(
                freshness_score=0.0,
                venue_accuracy_score=0.0,
                menu_validity_score=0.0,
                beverage_relevance_score=0.0,
                composite_score=0.0,
                failure_reason="fetch_failed",
                debug_meta={"fetch_error": fetch.error},
            )
            return bundle, fetch.status_code, fetch.final_url or src["website_url"], item.domain

        live_text = html_to_text(fetch.text)
        freshness_score = score_freshness(live_text, src["captured_text"])
        venue_accuracy_score, venue_meta = score_venue_accuracy(
            src["name"], src["address"], src["city"], live_text
        )
        menu_validity_score, menu_meta = score_menu_validity_rule(live_text)
        beverage_relevance_score, bev_meta = score_beverage_relevance(live_text)

        llm_used = False
        llm_result = None

        if needs_llm(freshness_score, menu_validity_score):
            llm_result = await self.haiku.classify(
                name=src["name"],
                address=src["address"],
                city=src["city"],
                state=src["state"],
                url=src["website_url"],
                page_text=live_text,
            )
            if llm_result:
                llm_used = True
                if llm_result.get("is_real_menu") is True:
                    menu_validity_score = max(menu_validity_score, 0.75)
                elif llm_result.get("is_real_menu") is False:
                    menu_validity_score = min(menu_validity_score, 0.20)
                if llm_result.get("is_correct_venue") is True:
                    venue_accuracy_score = max(venue_accuracy_score, 0.80)
                elif llm_result.get("is_correct_venue") is False:
                    venue_accuracy_score = min(venue_accuracy_score, 0.20)
                if llm_result.get("has_beverage_content") is True:
                    beverage_relevance_score = max(beverage_relevance_score, 0.75)
                elif llm_result.get("has_beverage_content") is False:
                    beverage_relevance_score = min(beverage_relevance_score, 0.20)
                if llm_result.get("is_boilerplate_only") is True:
                    menu_validity_score = min(menu_validity_score, 0.15)
                    menu_meta["boilerplate_hits"] = max(menu_meta.get("boilerplate_hits", 0), 99)
                if llm_result.get("is_aggregator_page") is True:
                    menu_validity_score = min(menu_validity_score, 0.10)
                    venue_accuracy_score = min(venue_accuracy_score, 0.30)
                    menu_meta["aggregator_hits"] = max(menu_meta.get("aggregator_hits", 0), 99)

        composite_score = compute_composite(
            freshness_score, venue_accuracy_score, menu_validity_score, beverage_relevance_score,
        )
        failure_reason = choose_failure_reason(
            freshness_score, venue_accuracy_score, menu_validity_score, beverage_relevance_score,
            fetch_failed=False, rule_meta=menu_meta,
        )

        bundle = ScoreBundle(
            freshness_score=round(freshness_score, 4),
            venue_accuracy_score=round(venue_accuracy_score, 4),
            menu_validity_score=round(menu_validity_score, 4),
            beverage_relevance_score=round(beverage_relevance_score, 4),
            composite_score=composite_score,
            failure_reason=failure_reason,
            llm_used=llm_used,
            debug_meta={
                "venue_meta": venue_meta,
                "menu_meta": menu_meta,
                "beverage_meta": bev_meta,
                "llm_result": llm_result,
                "capture_status": src["capture_status"],
                "artifact_url": src["artifact_url"],
                "captured_at": src["captured_at"].isoformat() if src["captured_at"] else None,
            },
        )

        final_url = fetch.final_url or src["website_url"]
        live_domain = urlparse(final_url).netloc.lower().replace("www.", "") if final_url else item.domain

        return bundle, fetch.status_code, final_url, live_domain

    async def get_status(self, run_id: int) -> dict[str, Any]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            run = await conn.fetchrow(GET_RUN_SQL, run_id)
            if not run:
                raise ValueError(f"Run not found: {run_id}")

            counts = await conn.fetchrow(RUN_STATUS_COUNTS_SQL, run_id)
            checkpoint = CheckpointStore(conn)
            last_processed = await checkpoint.get_last_processed_id(run_id)
            pass_row = await conn.fetchrow(PASS_RATE_SO_FAR_SQL, run_id)

            return {
                "run_id": run["id"],
                "run_type": run["run_type"],
                "status": run["status"],
                "sample_size": run["sample_size"],
                "state_filter": run["state_filter"],
                "started_at": run["started_at"],
                "completed_at": run["completed_at"],
                "processed_count": int(counts["processed_count"] or 0),
                "remaining_count": int(counts["remaining_count"] or 0),
                "pass_rate_so_far": float(pass_row["pass_rate"]) if pass_row and pass_row["pass_rate"] is not None else None,
                "checkpoint_last_processed_id": last_processed,
            }
