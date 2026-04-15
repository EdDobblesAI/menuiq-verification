from __future__ import annotations

import asyncpg

from app.sql import GENERATE_DAILY_REPORT_SQL, UPSERT_DAILY_REPORT_SQL


async def generate_daily_report(conn: asyncpg.Connection, report_date) -> dict | None:
    row = await conn.fetchrow(GENERATE_DAILY_REPORT_SQL, report_date)
    if not row or not row["report_json"]:
        return None
    return row["report_json"]


async def upsert_daily_report(conn: asyncpg.Connection, report_date, run_id: int, report_json: dict) -> None:
    await conn.execute(UPSERT_DAILY_REPORT_SQL, report_date, run_id, report_json)
