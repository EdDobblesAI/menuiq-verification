import asyncpg

from app.sql import ELIGIBLE_FULL_RUN_ITEMS_SQL, STRATIFIED_SAMPLE_RUN_ITEMS_SQL


async def materialize_run_items(
    conn: asyncpg.Connection,
    *,
    run_id: int,
    run_type: str,
    sample_size: int,
    state_filter: str | None,
) -> int:
    if run_type == "full":
        await conn.execute(ELIGIBLE_FULL_RUN_ITEMS_SQL, run_id, state_filter)
    else:
        await conn.execute(STRATIFIED_SAMPLE_RUN_ITEMS_SQL, run_id, sample_size, state_filter)

    row = await conn.fetchrow(
        "SELECT COUNT(*) AS cnt FROM qa.qa_run_items WHERE run_id = $1;",
        run_id,
    )
    return int(row["cnt"])
