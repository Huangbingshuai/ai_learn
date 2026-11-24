from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import select, tuple_

from app.api.attempts import AnswerReceipt
from app.api.dependencies import CurrentUser, DB
from app.api.errors import ApiError, ErrorView, not_found
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


class RecordPage(BaseModel):
    items: list[RecordItem]
    nextCursor: str | None = None


def _parse_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        point = b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True).decode("ascii")
        value, record_id = point.split("|")
        completed = datetime.fromisoformat(value)
        if completed.utcoffset() is None:
            raise ValueError("Expected a timezone")
        return completed, str(UUID(record_id))
    except (ValueError, UnicodeError, Base64Error):
        raise ApiError(422, "INVALID_CURSOR", "学习记录的翻页位置无效。") from None


@router.get("/records")
def list_records(
    db: DB,
    user_id: CurrentUser,
    cursor: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> RecordPage:
    statement = select(Record).where(Record.user_id == user_id)
    if cursor is not None:
        completed, record_id = _parse_cursor(cursor)
        statement = statement.where(tuple_(Record.completed_at, Record.id) < (completed, record_id))
    entries = db.scalars(
        statement.order_by(Record.completed_at.desc(), Record.id.desc()).limit(limit + 1)
    ).all()
    next_cursor = None
    if len(entries) > limit:
        last = entries[limit - 1]
        next_cursor = urlsafe_b64encode(f"{last.completed_at.isoformat()}|{last.id}".encode()).decode()
    return RecordPage(items=[_record_item(entry) for entry in entries[:limit]], nextCursor=next_cursor)
