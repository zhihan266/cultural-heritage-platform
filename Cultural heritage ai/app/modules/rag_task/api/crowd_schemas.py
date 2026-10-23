"""Allowlisted crowd HTTP contracts; no ORM metadata or private execution data."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

CrowdStatus = Literal["collecting", "aggregating", "ingesting", "completed", "failed"]
JobStatus = Literal["pending", "running", "retry", "uncertain", "succeeded", "failed"]
ParticipationStatus = Literal["accepted", "submitted"]


class Participation(BaseModel):
    status: str
    accepted_at: datetime
    submitted_at: datetime | None


class CrowdSummary(BaseModel):
    task_id: str
    question: str
    answer_format: str
    title: str | None
    status: CrowdStatus
    created_at: datetime | None
    is_publisher: bool
    required_worker_count: int
    accepted_worker_count: int
    valid_worker_count: int
    can_accept: bool
    can_submit: bool
    threshold_reached: bool
    aggregation_status: JobStatus
    ingestion_status: str | None
    result_available: bool
    my_participation: Participation | None


class CrowdPage(BaseModel):
    items: list[CrowdSummary]
    total: int
    page: int
    page_size: int


class PublicJob(BaseModel):
    phase: Literal["aggregate", "ingest"]
    status: JobStatus


class RetryEligibility(BaseModel):
    allowed: bool
    requires_acknowledgement: bool
    phase: Literal["aggregate", "ingest"] | None


class PublicResult(BaseModel):
    content: str
    answer_count: int
    version: int
    created_at: datetime


class PublicIngestion(BaseModel):
    status: str
    document_id: str | None
    source_id: str
    chunk_count: int | None


class CrowdDetail(CrowdSummary):
    my_answer: str | None
    jobs: list[PublicJob]
    retry: RetryEligibility
    result: PublicResult | None
    ingestion: PublicIngestion | None
    submission_reused: bool | None = None


class CrowdResultResponse(BaseModel):
    task_id: str
    status: CrowdStatus
    result: PublicResult | None
    ingestion: PublicIngestion | None
