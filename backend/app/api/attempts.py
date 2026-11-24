from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.api.dependencies import CurrentUser, DB, IdempotencyKey, digest
from app.api.errors import ApiError, ErrorView, not_found
from app.db import Attempt, GenerationJob, Quiz, Record, utcnow
from app.domain.quizzes import RULES_VERSION, Option, QuestionType, validate_quiz
from app.domain.topics import get_topic_version, load_fixed_quiz

router = APIRouter(responses={401: {"model": ErrorView}, 404: {"model": ErrorView}, 409: {"model": ErrorView}, 422: {"model": ErrorView}})
local_router = APIRouter(responses={401: {"model": ErrorView}, 409: {"model": ErrorView}, 422: {"model": ErrorView}})


class QuizView(BaseModel):
    quizId: str
    quizExpiresAt: datetime


class QuestionView(BaseModel):
    questionId: str
    type: QuestionType
    stem: str
    options: list[Option]
    index: int
    aiGenerated: bool


class StartAttempt(BaseModel):
    quizId: UUID


class AttemptView(QuizView):
    attemptId: str
    topicTitle: str
    status: Literal["ACTIVE", "PASSED", "ABANDONED"]
    answeredCount: int
    totalQuestions: int
    question: QuestionView | None = None
    recordId: str | None = None


def project_question(question: dict, index: int, ai_generated: bool) -> QuestionView:
    return QuestionView(
        questionId=question["questionId"],
        type=question["type"],
        stem=question["stem"],
        options=question["options"],
        index=index,
        aiGenerated=ai_generated,
    )


def expired_quiz() -> ApiError:
    return ApiError(409, "QUIZ_EXPIRED", "这组题已到期，请重新生成。")


def key_conflict() -> ApiError:
    return ApiError(409, "IDEMPOTENCY_CONFLICT", "此请求编号已用于其他内容。")


def _owned_quiz(db: DB, quiz_id: str, user_id: str) -> Quiz:
    quiz = db.scalar(select(Quiz).where(Quiz.id == quiz_id, Quiz.user_id == user_id))
    if quiz is None:
        raise not_found()
    return quiz


def _attempt_view(db: DB, attempt: Attempt, quiz: Quiz) -> AttemptView:
    questions = quiz.content["questions"]
    status = attempt.status
    if status == "ACTIVE" and quiz.expires_at <= utcnow():
        status = "ABANDONED"
    current = project_question(
        questions[attempt.answered_count], attempt.answered_count, quiz.ai_generated
    ) if status == "ACTIVE" else None
    record_id = db.scalar(select(Record.id).where(Record.attempt_id == attempt.id)) if status == "PASSED" else None
    return AttemptView(
        attemptId=attempt.id,
        quizId=quiz.id,
        quizExpiresAt=quiz.expires_at,
        topicTitle=quiz.topic_title,
        status=status,
        answeredCount=attempt.answered_count,
        totalQuestions=len(questions),
        question=current,
        recordId=record_id,
    )


@local_router.post("/dev/quizzes/fixed", status_code=201)
def fixed_quiz(db: DB, user_id: CurrentUser, idempotency_key: IdempotencyKey, request: Request) -> QuizView:
    with db.begin():
        now = utcnow()
        job_id = db.scalar(
            insert(GenerationJob).values(
                user_id=user_id,
                source="FIXED",
                topic_key="seasons",
                topic_version="v1",
                input_digest=digest("fixed-seasons"),
                idempotency_key=idempotency_key,
                status="SUCCEEDED",
                next_run_at=now,
                created_at=now,
                expires_at=now + timedelta(minutes=request.app.state.settings.job_minutes),
                model_name="fixed",
                rules_version=RULES_VERSION,
            ).on_conflict_do_nothing(constraint="uq_generation_idempotency").returning(GenerationJob.id)
        )
        if job_id is None:
            quiz = db.scalar(
                select(Quiz).join(GenerationJob, Quiz.job_id == GenerationJob.id).where(
                    GenerationJob.user_id == user_id,
                    GenerationJob.source == "FIXED",
                    GenerationJob.idempotency_key == idempotency_key,
                )
            )
            if quiz is None:
                raise expired_quiz()
        else:
            try:
                topic = get_topic_version("seasons", "v1")
                spec = validate_quiz(load_fixed_quiz(), topic)
            except (ValueError, OSError) as error:
                raise ApiError(503, "SOURCE_INSUFFICIENT", "固定题组的依据不可用。") from error
            quiz = Quiz(
                job_id=job_id,
                user_id=user_id,
                topic_key=topic["topicKey"],
                topic_title=topic["title"],
                content=spec.model_dump(mode="json"),
                evidence=topic["evidence"],
                ai_generated=False,
                created_at=now,
                expires_at=now + timedelta(hours=request.app.state.settings.quiz_hours),
                rules_version=RULES_VERSION,
            )
            db.add(quiz)
            db.flush()
        return QuizView(quizId=quiz.id, quizExpiresAt=quiz.expires_at)


@router.post("/attempts", status_code=201)
def start_attempt(body: StartAttempt, db: DB, user_id: CurrentUser, idempotency_key: IdempotencyKey) -> AttemptView:
    quiz_id = str(body.quizId)
    same_request = select(Attempt).where(
        Attempt.user_id == user_id, Attempt.idempotency_key == idempotency_key
    )
    with db.begin():
        attempt = db.scalar(same_request)
        if attempt is not None:
            if attempt.quiz_id != quiz_id:
                raise key_conflict()
            return _attempt_view(db, attempt, _owned_quiz(db, quiz_id, user_id))
        quiz = _owned_quiz(db, quiz_id, user_id)
        now = utcnow()
        if quiz.expires_at <= now:
            raise expired_quiz()
        attempt_id = db.scalar(
            insert(Attempt).values(
                user_id=user_id,
                quiz_id=quiz_id,
                idempotency_key=idempotency_key,
                created_at=now,
                rules_version=quiz.rules_version,
            ).on_conflict_do_nothing(constraint="uq_attempt_start_key").returning(Attempt.id)
        )
        attempt = db.get(Attempt, attempt_id) if attempt_id else db.scalar(same_request)
        if attempt.quiz_id != quiz_id:
            raise key_conflict()
        return _attempt_view(db, attempt, quiz)


@router.get("/attempts/{attempt_id}")
def read_attempt(attempt_id: UUID, db: DB, user_id: CurrentUser) -> AttemptView:
    attempt = db.scalar(select(Attempt).where(Attempt.id == str(attempt_id), Attempt.user_id == user_id))
    if attempt is None:
        raise not_found()
    return _attempt_view(db, attempt, _owned_quiz(db, attempt.quiz_id, user_id))
