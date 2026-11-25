from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.api.dependencies import DB, CurrentUser, IdempotencyKey, digest
from app.api.errors import ApiError, ErrorView, not_found
from app.db import (
    Attempt,
    AttemptAnswer,
    GenerationJob,
    Quiz,
    Record,
    RecordSummary,
    new_id,
    utcnow,
)
from app.domain.quizzes import RULES_VERSION, Identifier, Option, QuestionType, validate_quiz
from app.domain.summaries import RULES_VERSION as SUMMARY_RULES_VERSION
from app.domain.topics import EvidenceView, get_topic_version, load_fixed_quiz
from app.quota import QuotaReached, reserve_quota

router = APIRouter(
    responses={
        401: {"model": ErrorView},
        404: {"model": ErrorView},
        409: {"model": ErrorView},
        422: {"model": ErrorView},
        429: {"model": ErrorView},
    }
)
local_router = APIRouter(
    responses={401: {"model": ErrorView}, 409: {"model": ErrorView}, 422: {"model": ErrorView}}
)


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
    current = (
        project_question(
            questions[attempt.answered_count], attempt.answered_count, quiz.ai_generated
        )
        if status == "ACTIVE"
        else None
    )
    record_id = (
        db.scalar(select(Record.id).where(Record.attempt_id == attempt.id))
        if status == "PASSED"
        else None
    )
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
def fixed_quiz(
    db: DB, user_id: CurrentUser, idempotency_key: IdempotencyKey, request: Request
) -> QuizView:
    with db.begin():
        now = utcnow()
        job_id = db.scalar(
            insert(GenerationJob)
            .values(
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
            )
            .on_conflict_do_nothing(constraint="uq_generation_idempotency")
            .returning(GenerationJob.id)
        )
        if job_id is None:
            quiz = db.scalar(
                select(Quiz)
                .join(GenerationJob, Quiz.job_id == GenerationJob.id)
                .where(
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
def start_attempt(
    body: StartAttempt,
    db: DB,
    user_id: CurrentUser,
    idempotency_key: IdempotencyKey,
    request: Request,
) -> AttemptView:
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
        settings = request.app.state.settings
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            reserve_quota(
                db,
                f"attempt:user:{user_id}",
                start,
                start + timedelta(days=1),
                settings.user_daily_attempt_limit,
            )
        except QuotaReached:
            raise ApiError(429, "RATE_LIMITED", "今天的练习次数已达上限，请明天再试。", True)
        attempt_id = db.scalar(
            insert(Attempt)
            .values(
                user_id=user_id,
                quiz_id=quiz_id,
                idempotency_key=idempotency_key,
                created_at=now,
                rules_version=quiz.rules_version,
            )
            .on_conflict_do_nothing(constraint="uq_attempt_start_key")
            .returning(Attempt.id)
        )
        attempt = db.get(Attempt, attempt_id) if attempt_id else db.scalar(same_request)
        if attempt.quiz_id != quiz_id:
            raise key_conflict()
        return _attempt_view(db, attempt, quiz)


@router.get("/attempts/{attempt_id}")
def read_attempt(attempt_id: UUID, db: DB, user_id: CurrentUser) -> AttemptView:
    attempt = db.scalar(
        select(Attempt).where(Attempt.id == str(attempt_id), Attempt.user_id == user_id)
    )
    if attempt is None:
        raise not_found()
    return _attempt_view(db, attempt, _owned_quiz(db, attempt.quiz_id, user_id))


@router.get("/quizzes/{quiz_id}/latest-attempt")
def latest_attempt(quiz_id: UUID, db: DB, user_id: CurrentUser) -> AttemptView:
    quiz = _owned_quiz(db, str(quiz_id), user_id)
    attempt = db.scalar(
        select(Attempt)
        .where(Attempt.quiz_id == quiz.id, Attempt.user_id == user_id, Attempt.status == "ACTIVE")
        .order_by(Attempt.created_at.desc(), Attempt.id.desc())
        .limit(1)
    )
    if attempt is None:
        raise not_found()
    return _attempt_view(db, attempt, quiz)


class SubmitAnswer(BaseModel):
    selectedOptionIds: list[Identifier] = Field(min_length=1, max_length=24)


class AnswerReceipt(BaseModel):
    question: QuestionView
    selectedOptionIds: list[str]
    correctOptionIds: list[str]
    isCorrect: bool
    explanation: str
    evidence: list[EvidenceView]
    answeredCount: int
    totalQuestions: int
    recordId: str | None = None


def _save_completed_record(
    db: DB, attempt: Attempt, quiz: Quiz, receipt: AnswerReceipt, settings
) -> None:
    now = utcnow()
    earlier_answers = db.scalars(
        select(AttemptAnswer.receipt)
        .where(AttemptAnswer.attempt_id == attempt.id)
        .order_by(AttemptAnswer.question_index)
    ).all()
    record_id = new_id()
    receipt.recordId = record_id
    takeaways = quiz.content["takeaways"]
    source_ids = {evidence_id for item in takeaways for evidence_id in item["evidenceIds"]}
    record = Record(
        id=record_id,
        attempt_id=attempt.id,
        user_id=attempt.user_id,
        topic_title=quiz.topic_title,
        completed_at=now,
        total_questions=receipt.totalQuestions,
        correct_count=attempt.correct_count,
        takeaways=takeaways,
        takeaway_evidence=[
            source for source in quiz.evidence if source["evidenceId"] in source_ids
        ],
        answers=[*earlier_answers, receipt.model_dump(mode="json")],
    )
    db.add(record)
    db.flush()
    db.add(
        RecordSummary(
            record_id=record_id,
            status="QUEUED",
            model_name=settings.ai_model,
            rules_version=SUMMARY_RULES_VERSION,
            created_at=now,
            next_run_at=now,
            expires_at=now + timedelta(minutes=settings.job_minutes),
        )
    )


@router.put("/attempts/{attempt_id}/answers/{question_id}")
def submit_answer(
    attempt_id: UUID,
    question_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
    body: SubmitAnswer,
    db: DB,
    user_id: CurrentUser,
    request: Request,
) -> AnswerReceipt:
    with db.begin():
        attempt = db.scalar(
            select(Attempt)
            .where(Attempt.id == str(attempt_id), Attempt.user_id == user_id)
            .with_for_update()
        )
        if attempt is None:
            raise not_found()
        previous = db.get(AttemptAnswer, (attempt.id, question_id))
        if previous is not None:
            return AnswerReceipt.model_validate(previous.receipt)
        if attempt.status != "ACTIVE":
            raise ApiError(409, "ATTEMPT_CLOSED", "本次练习已结束。")
        quiz = _owned_quiz(db, attempt.quiz_id, user_id)
        if quiz.expires_at <= utcnow():
            raise expired_quiz()
        questions = quiz.content["questions"]
        index = attempt.answered_count
        question = questions[index]
        if question["questionId"] != question_id:
            raise ApiError(409, "OUT_OF_ORDER", "请先完成当前题目。")

        chosen = set(body.selectedOptionIds)
        ordered = [option["optionId"] for option in question["options"]]
        if not chosen <= set(ordered) or (question["type"] != "multi" and len(chosen) != 1):
            raise ApiError(422, "INVALID_SELECTION", "请选择符合当前题型的选项。")
        correct = set(question["correctOptionIds"])
        evidence = {item["evidenceId"]: item for item in quiz.evidence}
        attempt.answered_count += 1
        attempt.correct_count += int(chosen == correct)
        receipt = AnswerReceipt(
            question=project_question(question, index, quiz.ai_generated),
            selectedOptionIds=[item for item in ordered if item in chosen],
            correctOptionIds=[item for item in ordered if item in correct],
            isCorrect=chosen == correct,
            explanation=question["explanation"],
            evidence=[
                EvidenceView.model_validate(evidence[item]) for item in question["evidenceIds"]
            ],
            answeredCount=attempt.answered_count,
            totalQuestions=len(questions),
        )
        if attempt.answered_count == len(questions):
            attempt.status = "PASSED"
            _save_completed_record(db, attempt, quiz, receipt, request.app.state.settings)
        db.add(
            AttemptAnswer(
                attempt_id=attempt.id,
                question_id=question_id,
                question_index=index,
                receipt=receipt.model_dump(mode="json"),
            )
        )
        return receipt
