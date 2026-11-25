from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.api.attempts import key_conflict
from app.api.dependencies import DB, CurrentUser, IdempotencyKey, digest
from app.api.errors import ApiError, ErrorView, not_found
from app.db import GenerationJob, Quiz, SavedTopic, User, utcnow
from app.domain.quizzes import RULES_VERSION
from app.domain.topics import (
    SAVED_SOURCE_MAX_AGE,
    normalize,
)
from app.quota import reserve_quota

router = APIRouter(
    responses={
        401: {"model": ErrorView},
        404: {"model": ErrorView},
        409: {"model": ErrorView},
        413: {"model": ErrorView},
        422: {"model": ErrorView},
        429: {"model": ErrorView},
        503: {"model": ErrorView},
    }
)


class GenerateQuiz(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    topic: str | None = Field(default=None, min_length=2, max_length=80)
    topicKey: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,64}$")

    @model_validator(mode="after")
    def require_one_input(self):
        if (self.topic is None) == (self.topicKey is None):
            raise ValueError("Choose a saved topic or enter a topic")
        return self


class GenerationView(BaseModel):
    jobId: str
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    topicTitle: str
    phase: Literal["RESOLVING_TOPIC", "READING_SOURCES", "GENERATING", "VERIFYING"] | None = None
    createdAt: datetime
    quizId: str | None = None
    quizExpiresAt: datetime | None = None
    error: ErrorView | None = None


def _job_statement(user_id: str):
    return (
        select(GenerationJob, Quiz)
        .outerjoin(Quiz, Quiz.job_id == GenerationJob.id)
        .where(GenerationJob.user_id == user_id, GenerationJob.source == "TOPIC")
    )


def _topic_title(key: str | None) -> str:
    return "学习主题"


def _job_view(job: GenerationJob, quiz: Quiz | None, request: Request) -> GenerationView:
    result = GenerationView(
        jobId=job.id,
        status=job.status,
        topicTitle=quiz.topic_title
        if quiz
        else (job.topic_title or job.topic_input or _topic_title(job.topic_key)),
        phase=job.phase,
        createdAt=job.created_at,
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
            result.error = ApiError(409, "QUIZ_EXPIRED", "这组题已到期，请重新生成。", True).view(
                request_id
            )
        else:
            result.quizId = quiz.id
            result.quizExpiresAt = quiz.expires_at
    return result


@router.post("/generation-jobs", status_code=202)
def create_job(
    body: GenerateQuiz,
    db: DB,
    user_id: CurrentUser,
    idempotency_key: IdempotencyKey,
    request: Request,
) -> GenerationView:
    normalized = normalize(body.topic) if body.topic else None
    if normalized is not None and not any(char.isalnum() for char in normalized):
        raise ApiError(422, "INVALID_INPUT", "请输入想学习的主题或问题。")
    input_digest = digest(normalized if normalized is not None else f"saved:{body.topicKey}")
    same_job = _job_statement(user_id).where(GenerationJob.idempotency_key == idempotency_key)
    settings = request.app.state.settings

    with db.begin():
        # Serialize generation creation per account; a second device sees the first job.
        db.scalar(select(User).where(User.id == user_id).with_for_update())
        prior = db.execute(same_job).one_or_none()
        if prior is not None:
            if prior[0].input_digest != input_digest:
                raise key_conflict()
            return _job_view(*prior, request)

        now = utcnow()
        in_progress = db.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.user_id == user_id,
                GenerationJob.source == "TOPIC",
                GenerationJob.status.in_(("QUEUED", "RUNNING")),
                GenerationJob.expires_at > now,
            )
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )
        if in_progress is not None:
            raise ApiError(409, "GENERATION_IN_PROGRESS", "已有一组题目正在生成。")

        saved = None
        if body.topicKey:
            saved = db.get(SavedTopic, (user_id, body.topicKey))
            if saved is None:
                raise not_found()
        topic_key = saved.topic_key if saved else None
        topic_title = saved.title if saved else None
        topic_input = body.topic
        topic_version = None
        checkpoint = None
        if saved:
            if now - saved.evidence_at <= SAVED_SOURCE_MAX_AGE:
                checkpoint = {
                    "topic": {
                        "topicKey": saved.topic_key,
                        "title": saved.title,
                        "originalInput": saved.original_input,
                        "scope": saved.scope,
                        "evidence": saved.evidence,
                        "version": "web-v1",
                        "evidenceAt": saved.evidence_at.isoformat(),
                    }
                }
                topic_input = None
            else:
                checkpoint = {"savedScope": saved.scope}
                topic_input = saved.original_input or saved.title[:80]
        job_id = db.scalar(
            insert(GenerationJob)
            .values(
                user_id=user_id,
                source="TOPIC",
                topic_key=topic_key,
                topic_version=topic_version,
                topic_title=topic_title,
                topic_input=topic_input,
                checkpoint=checkpoint,
                input_digest=input_digest,
                idempotency_key=idempotency_key,
                status="QUEUED",
                phase=None,
                next_run_at=now,
                created_at=now,
                expires_at=now + timedelta(minutes=settings.job_minutes),
                model_name=settings.ai_model,
                rules_version=RULES_VERSION,
            )
            .on_conflict_do_nothing(constraint="uq_generation_idempotency")
            .returning(GenerationJob.id)
        )
        if job_id is None:
            job, quiz = db.execute(same_job).one()
            if job.input_digest != input_digest:
                raise key_conflict()
            return _job_view(job, quiz, request)

        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        reserve_quota(db, "generation:global", start, end, settings.global_daily_generation_limit)
        reserve_quota(
            db, f"generation:user:{user_id}", start, end, settings.user_daily_generation_limit
        )
        return _job_view(db.get(GenerationJob, job_id), None, request)


@router.get("/generation-jobs/current")
def current_job(db: DB, user_id: CurrentUser, request: Request) -> GenerationView | None:
    now = utcnow()
    result = db.execute(
        _job_statement(user_id)
        .where(or_(
            and_(GenerationJob.status.in_(("QUEUED", "RUNNING")), GenerationJob.expires_at > now),
            and_(GenerationJob.status == "SUCCEEDED", Quiz.expires_at > now),
            and_(GenerationJob.status == "FAILED", GenerationJob.created_at > now - timedelta(hours=24)),
        ))
        .order_by(
            (GenerationJob.status.in_(("QUEUED", "RUNNING"))).desc(),
            GenerationJob.created_at.desc(),
        )
        .limit(1)
    ).one_or_none()
    return _job_view(*result, request) if result else None


@router.get("/generation-jobs/{job_id}")
def read_job(job_id: UUID, db: DB, user_id: CurrentUser, request: Request) -> GenerationView:
    result = db.execute(
        _job_statement(user_id).where(GenerationJob.id == str(job_id))
    ).one_or_none()
    if result is None:
        raise not_found()
    return _job_view(*result, request)
