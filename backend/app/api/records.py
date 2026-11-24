from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.attempts import AnswerReceipt
from app.api.dependencies import CurrentUser, DB
from app.api.errors import ErrorView, not_found
from app.db import Record, RecordSummary
from app.domain.quizzes import Takeaway
from app.domain.summaries import SummaryContent

router = APIRouter(responses={401: {"model": ErrorView}, 404: {"model": ErrorView}, 422: {"model": ErrorView}})
JobStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]


class SummaryView(BaseModel):
    status: JobStatus
    content: SummaryContent | None = None
    error: ErrorView | None = None


class RecordItem(BaseModel):
    recordId: str
    topicTitle: str
    completedAt: datetime
    totalQuestions: int
    correctCount: int


class RecordView(RecordItem):
    takeaways: list[Takeaway]
    answers: list[AnswerReceipt]
    aiSummary: SummaryView


def _record_item(record: Record) -> RecordItem:
    return RecordItem(
        recordId=record.id,
        topicTitle=record.topic_title,
        completedAt=record.completed_at,
        totalQuestions=record.total_questions,
        correctCount=record.correct_count,
    )


def _summary_view(summary: RecordSummary | None, request: Request) -> SummaryView:
    if summary is None:
        return SummaryView(status="FAILED")
    error = ErrorView(
        code=summary.error_code or "SUMMARY_FAILED",
        message=summary.error_message or "这次总结没有生成。",
        requestId=request.state.request_id,
        retryable=summary.error_retryable,
    ) if summary.status == "FAILED" else None
    return SummaryView(
        status=summary.status,
        content=summary.content if summary.status == "SUCCEEDED" else None,
        error=error,
    )


@router.get("/records/{record_id}")
def read_record(record_id: UUID, db: DB, user_id: CurrentUser, request: Request) -> RecordView:
    result = db.execute(
        select(Record, RecordSummary)
        .outerjoin(RecordSummary, RecordSummary.record_id == Record.id)
        .where(Record.id == str(record_id), Record.user_id == user_id)
    ).one_or_none()
    if result is None:
        raise not_found()
    record, summary = result
    return RecordView(
        **_record_item(record).model_dump(),
        takeaways=record.takeaways,
        answers=record.answers,
        aiSummary=_summary_view(summary, request),
    )
