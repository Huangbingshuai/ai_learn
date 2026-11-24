from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.api.attempts import key_conflict
from app.api.auth import reserve_quota
from app.api.dependencies import CurrentUser, DB, IdempotencyKey, digest
from app.api.errors import ApiError, ErrorView, not_found
from app.db import GenerationJob, Quiz, utcnow
from app.domain.quizzes import RULES_VERSION
from app.domain.topics import load_topics, match_topic, normalize

router = APIRouter(responses={
    401: {"model": ErrorView}, 404: {"model": ErrorView}, 409: {"model": ErrorView},
    413: {"model": ErrorView}, 422: {"model": ErrorView}, 429: {"model": ErrorView},
    503: {"model": ErrorView},
})


class GenerateQuiz(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    topic: str = Field(min_length=2, max_length=80)


class GenerationView(BaseModel):
    jobId: str
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    topicTitle: str
    phase: Literal["READING_SOURCES", "GENERATING"] | None = None
    quizId: str | None = None
    quizExpiresAt: datetime | None = None
    error: ErrorView | None = None


def _job_statement(user_id: str):
    return (
        select(GenerationJob, Quiz)
        .outerjoin(Quiz, Quiz.job_id == GenerationJob.id)
        .where(GenerationJob.user_id == user_id, GenerationJob.source == "TOPIC")
    )


def _topic_title(key: str) -> str:
    try:
        return next((topic.title for topic in load_topics() if topic.topicKey == key), "学习主题")
    except ValueError:
        return "学习主题"


def _job_view(job: GenerationJob, quiz: Quiz | None, request: Request, title=None) -> GenerationView:
    result = GenerationView(
        jobId=job.id,
        status=job.status,
        topicTitle=quiz.topic_title if quiz else title or _topic_title(job.topic_key),
        phase=job.phase,
    )
    now = utcnow()
    request_id = request.state.request_id
    if job.status in {"QUEUED", "RUNNING"} and job.expires_at <= now:
        result.status = "FAILED"
        result.phase = None
        result.error = ApiError(
            409, "PROVIDER_TIMEOUT", "生成超时了，可以重新生成或换个主题。", True
        ).view(request_id)
    elif job.status == "FAILED":
        result.error = ErrorView(
            code=job.error_code or "GENERATION_FAILED",
            message=job.error_message or "题组暂时没有生成。",
            requestId=request_id,
            retryable=job.error_retryable,
        )
    elif job.status == "SUCCEEDED":
        if quiz is None or quiz.expires_at <= now:
            result.status = "FAILED"
            result.error = ApiError(409, "QUIZ_EXPIRED", "这组题已到期，请重新生成。", True).view(request_id)
        else:
            result.quizId = quiz.id
            result.quizExpiresAt = quiz.expires_at
    return result


@router.post("/generation-jobs", status_code=202)
def create_job(
    body: GenerateQuiz, db: DB, user_id: CurrentUser, idempotency_key: IdempotencyKey, request: Request
) -> GenerationView:
    input_digest = digest(normalize(body.topic))
    same_job = _job_statement(user_id).where(GenerationJob.idempotency_key == idempotency_key)
    settings = request.app.state.settings

    with db.begin():
        prior = db.execute(same_job).one_or_none()
        if prior is not None:
            if prior[0].input_digest != input_digest:
                raise key_conflict()
            return _job_view(*prior, request)

        try:
            topic = match_topic(body.topic)
            if topic is None:
                choices = "、".join(f"「{item.title}」" for item in load_topics())
                raise ApiError(422, "TOPIC_UNSUPPORTED", f"暂时没有匹配的主题。试试{choices}。")
        except ValueError:
            raise ApiError(503, "SOURCE_INSUFFICIENT", "学习主题暂时不可用。") from None

        now = utcnow()
        job_id = db.scalar(
            insert(GenerationJob).values(
                user_id=user_id,
                source="TOPIC",
                topic_key=topic.topicKey,
                topic_version=topic.version,
                input_digest=input_digest,
                idempotency_key=idempotency_key,
                status="QUEUED",
                phase="READING_SOURCES",
                next_run_at=now,
                created_at=now,
                expires_at=now + timedelta(minutes=settings.job_minutes),
                model_name=settings.deepseek_model,
                rules_version=RULES_VERSION,
            ).on_conflict_do_nothing(constraint="uq_generation_idempotency").returning(GenerationJob.id)
        )
        if job_id is None:
            job, quiz = db.execute(same_job).one()
            if job.input_digest != input_digest:
                raise key_conflict()
            return _job_view(job, quiz, request)

        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        reserve_quota(db, "generation:global", start, end, settings.global_daily_generation_limit)
        reserve_quota(db, f"generation:user:{user_id}", start, end, settings.user_daily_generation_limit)
        return _job_view(db.get(GenerationJob, job_id), None, request, title=topic.title)


@router.get("/generation-jobs/{job_id}")
def read_job(job_id: UUID, db: DB, user_id: CurrentUser, request: Request) -> GenerationView:
    result = db.execute(_job_statement(user_id).where(GenerationJob.id == str(job_id))).one_or_none()
    if result is None:
        raise not_found()
    return _job_view(*result, request)
