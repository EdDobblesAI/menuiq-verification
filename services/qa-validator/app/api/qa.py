import asyncio
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_admin_key
from app.db import get_pool
from app.orchestrator import RunOrchestrator
from app.sampling import materialize_run_items
from app.schemas import (
    DailyReportResponse,
    HealthResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunStatusResponse,
)
from app.sql import ACTIVE_RUN_SQL, CREATE_SAMPLE_RUN_SQL, REPORT_BY_DATE_SQL, REPORT_LATEST_SQL

router = APIRouter()


@router.post("/qa/runs", response_model=RunCreateResponse, dependencies=[Depends(require_admin_key)])
async def create_run(payload: RunCreateRequest) -> RunCreateResponse:
    pool = await get_pool()

    async with pool.acquire() as conn:
        active = await conn.fetchrow(ACTIVE_RUN_SQL)
        if active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Another run is active: {active['id']}",
            )

        run_id = await conn.fetchval(
            CREATE_SAMPLE_RUN_SQL,
            payload.run_type,
            payload.sample_size,
            payload.state_filter,
            "admin_api",
        )
        actual_size = await materialize_run_items(
            conn,
            run_id=run_id,
            run_type=payload.run_type,
            sample_size=payload.sample_size,
            state_filter=payload.state_filter,
        )
        await conn.execute(
            "UPDATE qa.qa_sample_runs SET sample_size = $2 WHERE id = $1;",
            run_id,
            actual_size,
        )

    orchestrator = RunOrchestrator()
    asyncio.create_task(orchestrator.run(run_id))

    return RunCreateResponse(run_id=run_id, status="pending")


@router.get("/qa/runs/{run_id}/status", response_model=RunStatusResponse, dependencies=[Depends(require_admin_key)])
async def get_run_status(run_id: int) -> RunStatusResponse:
    orchestrator = RunOrchestrator()
    try:
        data = await orchestrator.get_status(run_id)
        return RunStatusResponse(**data)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/qa/reports/latest", response_model=DailyReportResponse, dependencies=[Depends(require_admin_key)])
async def get_latest_report() -> DailyReportResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(REPORT_LATEST_SQL)
        if not row:
            raise HTTPException(status_code=404, detail="No reports found")
        return DailyReportResponse(**dict(row))


@router.get("/qa/reports/{report_date}", response_model=DailyReportResponse, dependencies=[Depends(require_admin_key)])
async def get_report_by_date(report_date: date) -> DailyReportResponse:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(REPORT_BY_DATE_SQL, report_date)
        if not row:
            raise HTTPException(status_code=404, detail="Report not found")
        return DailyReportResponse(**dict(row))


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", app="qa-validator")
