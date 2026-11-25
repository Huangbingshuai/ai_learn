"""Durable, idempotent collection and backfill of wrong answers."""

import argparse
import logging
from datetime import datetime

from sqlalchemy import case, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import (
    Attempt,
    AttemptAnswer,
    MistakeAnswerEvent,
    MistakeItem,
    Quiz,
    Record,
    make_session_factory,
    utcnow,
)
from app.domain.questions import question_fingerprint
from app.domain.topics import live_topic_key

logger = logging.getLogger(__name__)


def _question_snapshot_from_receipt(receipt: dict) -> dict:
    projected = receipt["question"]
    return {
        "questionId": projected["questionId"],
        "type": projected["type"],
        "stem": projected["stem"],
        "options": projected["options"],
        "correctOptionIds": receipt["correctOptionIds"],
        "explanation": receipt["explanation"],
        "evidenceIds": [item["evidenceId"] for item in receipt["evidence"]],
        "aiGenerated": projected["aiGenerated"],
    }


def record_wrong_answer(
    db: Session,
    *,
    user_id: str,
    attempt_id: str,
    question: dict,
    receipt: dict,
    topic_key: str,
    topic_title: str,
    answered_at: datetime | None = None,
    reopen_mastered: bool = True,
) -> bool:
    """Record one accepted incorrect answer; return False for an already seen attempt answer."""
    if receipt["isCorrect"]:
        return False

    when = answered_at or utcnow()
    fingerprint = question_fingerprint(question)
    event_id = db.scalar(
        insert(MistakeAnswerEvent)
        .values(
            user_id=user_id,
            attempt_id=attempt_id,
            question_id=question["questionId"],
            fingerprint=fingerprint,
            question_snapshot=question,
            receipt_snapshot=receipt,
            topic_key=topic_key,
            topic_title=topic_title,
            answered_at=when,
        )
        .on_conflict_do_nothing(constraint="uq_mistake_answer_event")
        .returning(MistakeAnswerEvent.id)
    )
    if event_id is None:
        return False

    statement = insert(MistakeItem).values(
        user_id=user_id,
        fingerprint=fingerprint,
        question_snapshot=question,
        latest_receipt=receipt,
        topic_key=topic_key,
        topic_title=topic_title,
        wrong_count=1,
        first_wrong_at=when,
        last_wrong_at=when,
        status="TO_REVIEW",
        mastered_at=None,
        created_at=when,
        updated_at=when,
    )
    db.execute(
        statement.on_conflict_do_update(
            constraint="uq_mistake_user_fingerprint",
            set_={
                "wrong_count": MistakeItem.wrong_count + 1,
                "first_wrong_at": func.least(
                    MistakeItem.first_wrong_at, statement.excluded.first_wrong_at
                ),
                "last_wrong_at": case(
                    (
                        statement.excluded.last_wrong_at >= MistakeItem.last_wrong_at,
                        statement.excluded.last_wrong_at,
                    ),
                    else_=MistakeItem.last_wrong_at,
                ),
                "question_snapshot": case(
                    (
                        statement.excluded.last_wrong_at >= MistakeItem.last_wrong_at,
                        statement.excluded.question_snapshot,
                    ),
                    else_=MistakeItem.question_snapshot,
                ),
                "latest_receipt": case(
                    (
                        statement.excluded.last_wrong_at >= MistakeItem.last_wrong_at,
                        statement.excluded.latest_receipt,
                    ),
                    else_=MistakeItem.latest_receipt,
                ),
                "topic_key": case(
                    (
                        statement.excluded.last_wrong_at >= MistakeItem.last_wrong_at,
                        statement.excluded.topic_key,
                    ),
                    else_=MistakeItem.topic_key,
                ),
                "topic_title": case(
                    (
                        statement.excluded.last_wrong_at >= MistakeItem.last_wrong_at,
                        statement.excluded.topic_title,
                    ),
                    else_=MistakeItem.topic_title,
                ),
                "status": case((reopen_mastered, "TO_REVIEW"), else_=MistakeItem.status),
                "mastered_at": case((reopen_mastered, None), else_=MistakeItem.mastered_at),
                "updated_at": func.greatest(MistakeItem.updated_at, statement.excluded.updated_at),
            },
        )
    )
    return True


def _topic_key(db: Session, attempt_id: str, fallback_title: str) -> tuple[str, str]:
    quiz = db.scalar(
        select(Quiz).join(Attempt, Quiz.id == Attempt.quiz_id).where(Attempt.id == attempt_id)
    )
    if quiz is not None:
        return quiz.topic_key, fallback_title or quiz.topic_title
    return live_topic_key(fallback_title), fallback_title


def _backfill_record(db: Session, record: Record) -> int:
    topic_key, topic_title = _topic_key(db, record.attempt_id, record.topic_title)
    imported = 0
    for receipt in record.answers:
        if not isinstance(receipt, dict):
            logger.warning("Skipping non-object mistake receipt in record %s", record.id)
            continue
        if receipt.get("isCorrect") is not False:
            continue
        try:
            question = _question_snapshot_from_receipt(receipt)
            imported += record_wrong_answer(
                db,
                user_id=record.user_id,
                attempt_id=record.attempt_id,
                question=question,
                receipt=receipt,
                topic_key=topic_key,
                topic_title=topic_title,
                answered_at=record.completed_at,
                reopen_mastered=False,
            )
        except (KeyError, TypeError, ValueError) as error:
            logger.warning("Skipping invalid mistake in record %s: %s", record.id, error)
    return imported


def _backfill_incomplete_answers(db: Session, batch_size: int) -> int:
    imported = 0
    cursor: tuple[str, int] | None = None
    while True:
        statement = (
            select(AttemptAnswer, Attempt, Quiz)
            .join(Attempt, AttemptAnswer.attempt_id == Attempt.id)
            .join(Quiz, Attempt.quiz_id == Quiz.id)
            .where(~select(Record.id).where(Record.attempt_id == Attempt.id).exists())
            .order_by(AttemptAnswer.attempt_id, AttemptAnswer.question_index)
            .limit(batch_size)
        )
        if cursor is not None:
            statement = statement.where(
                tuple_(AttemptAnswer.attempt_id, AttemptAnswer.question_index) > cursor
            )
        rows = db.execute(statement).all()
        if not rows:
            break
        for answer, attempt, quiz in rows:
            cursor = (answer.attempt_id, answer.question_index)
            receipt = answer.receipt
            if not isinstance(receipt, dict):
                logger.warning("Skipping non-object mistake receipt in attempt %s", attempt.id)
                continue
            if receipt.get("isCorrect") is not False:
                continue
            try:
                question = _question_snapshot_from_receipt(receipt)
                imported += record_wrong_answer(
                    db,
                    user_id=attempt.user_id,
                    attempt_id=attempt.id,
                    question=question,
                    receipt=receipt,
                    topic_key=quiz.topic_key,
                    topic_title=quiz.topic_title,
                    answered_at=attempt.created_at,
                    reopen_mastered=False,
                )
            except (KeyError, TypeError, ValueError) as error:
                logger.warning("Skipping invalid mistake in attempt %s: %s", attempt.id, error)
    return imported


def backfill_mistakes(db: Session, *, batch_size: int = 500) -> int:
    """Idempotently import historical completed records and still-live attempts."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    imported = 0
    cursor: str | None = None
    while True:
        statement = select(Record).order_by(Record.id).limit(batch_size)
        if cursor is not None:
            statement = statement.where(Record.id > cursor)
        rows = db.scalars(statement).all()
        if not rows:
            break
        for record in rows:
            cursor = record.id
            imported += _backfill_record(db, record)
    imported += _backfill_incomplete_answers(db, batch_size)
    return imported


def main() -> None:
    parser = argparse.ArgumentParser(description="Idempotently backfill the mistake book.")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    settings = Settings()
    factory = make_session_factory(settings.database_url)
    with factory.begin() as db:
        imported = backfill_mistakes(db, batch_size=args.batch_size)
    print(f"Imported {imported} mistake answer events.")


if __name__ == "__main__":
    main()
