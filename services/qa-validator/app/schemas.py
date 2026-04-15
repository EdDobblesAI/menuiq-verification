from datetime import date, datetime
from typing import Literal, Any
from pydantic import BaseModel, Field


class RunCreateRequest(BaseModel):
    run_type: Literal["sample", "full"] = "sample"
    sample_size: int = Field(default=500, ge=1)
    state_filter: str | None = None


class RunCreateResponse(BaseModel):
    run_id: int
    status: str


class RunStatusResponse(BaseModel):
    run_id: int
    run_type: str
    status: str
    sample_size: int
    state_filter: str | None
    started_at: datetime | None
    completed_at: datetime | None
    processed_count: int
    remaining_count: int
    pass_rate_so_far: float | None
    checkpoint_last_processed_id: int | None


class DailyReportResponse(BaseModel):
    report_date: date
    run_id: int
    report_json: dict[str, Any]
    created_at: datetime


class HealthResponse(BaseModel):
    status: str
    app: str
