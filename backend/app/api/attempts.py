from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert

from app.api.dependencies import DB, CurrentUser, IdempotencyKey, digest
from app.api.errors import ApiError, ErrorView, not_found
from app.db import (
    Attempt,
    AttemptAnswer,
    CoinSettlement,
    FavoriteItem,
    GenerationJob,
    MistakeItem,
    Quiz,
    Record,
    RecordSummary,
    SavedTopic,
    User,
    new_id,
    utcnow,
)
from app.domain.questions import question_fingerprint
from app.domain.quizzes import RULES_VERSION, Identifier, Option, QuestionType
from app.domain.summaries import RULES_VERSION as SUMMARY_RULES_VERSION
from app.domain.topics import EvidenceView
from app.quota import QuotaReached, reserve_quota
from app.services.mistakes import record_wrong_answer

router = APIRouter(
    responses={
        401: {"model": ErrorView},
        404: {"model": ErrorView},
        409: {"model": ErrorView},
        422: {"model": ErrorView},
        429: {"model": ErrorView},
    }
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


class DraftAnswer(BaseModel):
    selectedOptionIds: list[Identifier] = Field(max_length=8)


class AttemptView(QuizView):
    attemptId: str
    topicTitle: str
    status: Literal["ACTIVE", "PASSED", "ABANDONED"]
    purpose: Literal["LEARNING", "PRACTICE"]
    answeredCount: int
    totalQuestions: int
    question: QuestionView | None = None
    draftOptionIds: list[str] = Field(default_factory=list)
    recordId: str | None = None


class ActiveAttempts(BaseModel):
    items: list[AttemptView]


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
        purpose=attempt.purpose,
        answeredCount=attempt.answered_count,
        totalQuestions=len(questions),
        question=current,
        draftOptionIds=list(attempt.draft_option_ids or []) if status == "ACTIVE" else [],
        recordId=record_id,
    )


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
            existing_quiz = _owned_quiz(db, quiz_id, user_id)
            if db.get(GenerationJob, existing_quiz.job_id).source == "PRACTICE":
                raise key_conflict()
            return _attempt_view(db, attempt, existing_quiz)
        quiz = _owned_quiz(db, quiz_id, user_id)
        if db.get(GenerationJob, quiz.job_id).source == "PRACTICE":
            raise not_found()
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


@router.post("/favorites/{fingerprint}/practice", status_code=201)
@router.post("/mistakes/{fingerprint}/practice", status_code=201)
def start_mistake_practice(
    fingerprint: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    db: DB,
    user_id: CurrentUser,
    idempotency_key: IdempotencyKey,
    request: Request,
) -> AttemptView:
    favorite_practice = request.url.path.startswith("/v1/favorites/")
    collection = FavoriteItem if favorite_practice else MistakeItem
    with db.begin():
        existing = db.scalar(
            select(Attempt)
            .where(Attempt.user_id == user_id, Attempt.idempotency_key == idempotency_key)
            .with_for_update()
        )
        if existing is not None:
            if existing.purpose != "PRACTICE":
                raise key_conflict()
            quiz = _owned_quiz(db, existing.quiz_id, user_id)
            job = db.get(GenerationJob, quiz.job_id)
            if job is None or job.source != "PRACTICE" or job.input_digest != fingerprint:
                raise key_conflict()
            return _attempt_view(db, existing, quiz)

        item = db.scalar(
            select(collection)
            .where(
                collection.user_id == user_id,
                collection.fingerprint == fingerprint,
                exists(
                    select(SavedTopic.topic_key).where(
                        SavedTopic.user_id == user_id,
                        SavedTopic.topic_key == collection.topic_key,
                    )
                ),
            )
            .with_for_update()
        )
        if item is None:
            raise not_found()

        # Another identical request may have completed while this request waited
        # for the item lock. Recheck before charging the daily attempt quota.
        existing = db.scalar(
            select(Attempt)
            .where(Attempt.user_id == user_id, Attempt.idempotency_key == idempotency_key)
            .with_for_update()
        )
        if existing is not None:
            if existing.purpose != "PRACTICE":
                raise key_conflict()
            quiz = _owned_quiz(db, existing.quiz_id, user_id)
            job = db.get(GenerationJob, quiz.job_id)
            if job is None or job.source != "PRACTICE" or job.input_digest != fingerprint:
                raise key_conflict()
            return _attempt_view(db, existing, quiz)

        now = utcnow()
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

        job_key = f"practice-{digest(fingerprint + ':' + idempotency_key)}"
        job_id = db.scalar(
            insert(GenerationJob)
            .values(
                user_id=user_id,
                source="PRACTICE",
                topic_key=item.topic_key,
                topic_title=item.topic_title,
                input_digest=fingerprint,
                idempotency_key=job_key,
                status="SUCCEEDED",
                next_run_at=now,
                created_at=now,
                expires_at=now + timedelta(minutes=settings.job_minutes),
                model_name="mistake-practice",
                rules_version=RULES_VERSION,
            )
            .on_conflict_do_nothing(constraint="uq_generation_idempotency")
            .returning(GenerationJob.id)
        )
        job = (
            db.get(GenerationJob, job_id)
            if job_id
            else db.scalar(
                select(GenerationJob)
                .where(
                    GenerationJob.user_id == user_id,
                    GenerationJob.source == "PRACTICE",
                    GenerationJob.idempotency_key == job_key,
                )
                .with_for_update()
            )
        )
        if job is None or job.input_digest != fingerprint:
            raise key_conflict()

        quiz = db.scalar(select(Quiz).where(Quiz.job_id == job.id))
        if quiz is None:
            question = dict(item.question_snapshot)
            question.setdefault("aiGenerated", False)
            quiz = Quiz(
                job_id=job.id,
                user_id=user_id,
                topic_key=item.topic_key,
                topic_title=item.topic_title,
                content={"questions": [question]},
                evidence=item.evidence if favorite_practice else item.latest_receipt.get("evidence", []),
                ai_generated=bool(question.get("aiGenerated", False)),
                created_at=now,
                expires_at=now + timedelta(hours=settings.quiz_hours),
                rules_version=RULES_VERSION,
            )
            db.add(quiz)
            db.flush()

        attempt_id = db.scalar(
            insert(Attempt)
            .values(
                user_id=user_id,
                quiz_id=quiz.id,
                idempotency_key=idempotency_key,
                status="ACTIVE",
                answered_count=0,
                correct_count=0,
                purpose="PRACTICE",
                created_at=now,
                rules_version=RULES_VERSION,
            )
            .on_conflict_do_nothing(constraint="uq_attempt_start_key")
            .returning(Attempt.id)
        )
        attempt = (
            db.get(Attempt, attempt_id)
            if attempt_id
            else db.scalar(
                select(Attempt).where(
                    Attempt.user_id == user_id,
                    Attempt.idempotency_key == idempotency_key,
                )
            )
        )
        if attempt is None or attempt.quiz_id != quiz.id:
            raise key_conflict()
        return _attempt_view(db, attempt, quiz)


@router.get("/attempts/active", response_model=ActiveAttempts)
def active_attempts(db: DB, user_id: CurrentUser) -> ActiveAttempts:
    rows = db.execute(
        select(Attempt, Quiz)
        .join(Quiz, Quiz.id == Attempt.quiz_id)
        .join(GenerationJob, GenerationJob.id == Quiz.job_id)
        .where(
            Attempt.user_id == user_id,
            Attempt.status == "ACTIVE",
            Attempt.purpose == "LEARNING",
            GenerationJob.source != "PRACTICE",
            Quiz.expires_at > utcnow(),
            exists().where(
                SavedTopic.user_id == user_id,
                SavedTopic.topic_key == Quiz.topic_key,
            ),
        )
        .order_by(Attempt.created_at.desc())
    ).all()
    return ActiveAttempts(items=[_attempt_view(db, attempt, quiz) for attempt, quiz in rows])


@router.get("/attempts/{attempt_id}")
def read_attempt(attempt_id: UUID, db: DB, user_id: CurrentUser) -> AttemptView:
    attempt = db.scalar(
        select(Attempt).where(Attempt.id == str(attempt_id), Attempt.user_id == user_id)
    )
    if attempt is None:
        raise not_found()
    quiz = _owned_quiz(db, attempt.quiz_id, user_id)
    if attempt.purpose == "LEARNING" and db.get(GenerationJob, quiz.job_id).source == "PRACTICE":
        raise not_found()
    return _attempt_view(db, attempt, quiz)


@router.put("/attempts/{attempt_id}/draft")
def save_attempt_draft(
    attempt_id: UUID, body: DraftAnswer, db: DB, user_id: CurrentUser
) -> AttemptView:
    with db.begin():
        attempt = db.scalar(
            select(Attempt)
            .where(Attempt.id == str(attempt_id), Attempt.user_id == user_id)
            .with_for_update()
        )
        if attempt is None:
            raise not_found()
        if attempt.status != "ACTIVE":
            raise ApiError(409, "ATTEMPT_CLOSED", "本次练习已结束。")
        quiz = _owned_quiz(db, attempt.quiz_id, user_id)
        if quiz.expires_at <= utcnow():
            raise expired_quiz()
        question = quiz.content["questions"][attempt.answered_count]
        valid_ids = {option["optionId"] for option in question["options"]}
        selected = list(dict.fromkeys(body.selectedOptionIds))
        if not set(selected) <= valid_ids or (question["type"] != "multi" and len(selected) > 1):
            raise ApiError(422, "INVALID_SELECTION", "请选择符合当前题型的选项。")
        attempt.draft_option_ids = selected
        return _attempt_view(db, attempt, quiz)


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
    coinDelta: int = 0
    coinBalance: int = 0
    coinSettlementNew: bool = False
    recordId: str | None = None


@router.get("/attempts/{attempt_id}/answers/{question_index}")
def read_attempt_answer(
    attempt_id: UUID, question_index: int, db: DB, user_id: CurrentUser
) -> AnswerReceipt:
    attempt = db.scalar(
        select(Attempt).where(Attempt.id == str(attempt_id), Attempt.user_id == user_id)
    )
    if attempt is None:
        raise not_found()
    answer = db.scalar(
        select(AttemptAnswer).where(
            AttemptAnswer.attempt_id == attempt.id,
            AttemptAnswer.question_index == question_index,
        )
    )
    if answer is None:
        raise not_found()
    receipt = AnswerReceipt.model_validate(answer.receipt)
    receipt.coinSettlementNew = False
    return receipt


def _settle_coins(
    db: DB, user_id: str, question: dict, is_correct: bool
) -> tuple[int, int, bool]:
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise not_found()
    fingerprint = question_fingerprint(question)
    previous = db.scalar(
        select(CoinSettlement).where(
            CoinSettlement.user_id == user_id,
            CoinSettlement.question_fingerprint == fingerprint,
        )
    )
    if previous is not None:
        return 0, user.coin_balance, False
    delta = 10 if is_correct else -min(5, user.coin_balance)
    user.coin_balance += delta
    db.add(
        CoinSettlement(
            user_id=user_id,
            question_fingerprint=fingerprint,
            is_correct=is_correct,
            coin_delta=delta,
            created_at=utcnow(),
        )
    )
    return delta, user.coin_balance, True


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
            receipt = AnswerReceipt.model_validate(previous.receipt)
            receipt.coinSettlementNew = False
            return receipt
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
        attempt.draft_option_ids = []
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
            coinDelta=0,
            coinBalance=0,
            coinSettlementNew=False,
        )
        if attempt.purpose == "PRACTICE":
            receipt.coinBalance = db.get(User, user_id).coin_balance
        else:
            receipt.coinDelta, receipt.coinBalance, receipt.coinSettlementNew = _settle_coins(
                db, user_id, question, receipt.isCorrect
            )
        if attempt.answered_count == len(questions):
            attempt.status = "PASSED"
            if attempt.purpose != "PRACTICE":
                _save_completed_record(db, attempt, quiz, receipt, request.app.state.settings)
        db.add(
            AttemptAnswer(
                attempt_id=attempt.id,
                question_id=question_id,
                question_index=index,
                receipt=receipt.model_dump(mode="json"),
            )
        )
        if not receipt.isCorrect:
            record_wrong_answer(
                db,
                user_id=user_id,
                attempt_id=attempt.id,
                question=question,
                receipt=receipt.model_dump(mode="json"),
                topic_key=quiz.topic_key,
                topic_title=quiz.topic_title,
                answered_at=utcnow(),
            )
        return receipt
