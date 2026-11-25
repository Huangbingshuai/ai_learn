from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("app_id", "openid", name="uq_users_wechat_identity"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    app_id: Mapped[str] = mapped_column(String(80))
    openid: Mapped[str] = mapped_column(String(128))
    nickname: Mapped[str | None] = mapped_column(String(30))
    coin_balance: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class CoinSettlement(Base):
    __tablename__ = "coin_settlements"
    __table_args__ = (
        UniqueConstraint("user_id", "question_fingerprint", name="uq_coin_user_question"),
        CheckConstraint("coin_delta BETWEEN -5 AND 10", name="ck_coin_delta"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    question_fingerprint: Mapped[str] = mapped_column(String(64))
    is_correct: Mapped[bool] = mapped_column(Boolean)
    coin_delta: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MistakeStatus(StrEnum):
    TO_REVIEW = "TO_REVIEW"
    MASTERED = "MASTERED"


class MistakeItem(Base):
    __tablename__ = "mistake_items"
    __table_args__ = (
        UniqueConstraint("user_id", "fingerprint", name="uq_mistake_user_fingerprint"),
        CheckConstraint("wrong_count > 0", name="ck_mistake_wrong_count"),
        CheckConstraint(
            "status IN ('TO_REVIEW', 'MASTERED')", name="ck_mistake_status"
        ),
        Index(
            "ix_mistakes_user_recent",
            "user_id",
            text("last_wrong_at DESC"),
            text("fingerprint DESC"),
        ),
        Index("ix_mistakes_user_status", "user_id", "status"),
        Index("ix_mistakes_user_topic", "user_id", "topic_key"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    question_snapshot: Mapped[dict] = mapped_column(JSONB)
    latest_receipt: Mapped[dict] = mapped_column(JSONB)
    topic_key: Mapped[str] = mapped_column(String(64))
    topic_title: Mapped[str] = mapped_column(String(100))
    wrong_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    first_wrong_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_wrong_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(12), default=MistakeStatus.TO_REVIEW.value, server_default="TO_REVIEW"
    )
    mastered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RateLimit(Base):
    __tablename__ = "rate_limits"
    __table_args__ = (Index("ix_rate_limits_expires_at", "expires_at"),)

    key: Mapped[str] = mapped_column(String(180), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def make_session_factory(database_url: str) -> sessionmaker:
    engine = create_engine(database_url, pool_pre_ping=True, hide_parameters=True)
    return sessionmaker(engine, expire_on_commit=False)


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "idempotency_key", name="uq_generation_idempotency"),
        CheckConstraint("source IN ('TOPIC', 'FIXED')", name="ck_generation_source"),
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')", name="ck_generation_status"
        ),
        Index("ix_generation_dispatch", "status", "next_run_at", "created_at"),
        Index("ix_generation_lease", "status", "lease_until"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(10))
    topic_key: Mapped[str | None] = mapped_column(String(64))
    topic_version: Mapped[str | None] = mapped_column(String(32))
    topic_input: Mapped[str | None] = mapped_column(String(80))
    topic_title: Mapped[str | None] = mapped_column(String(100))
    search_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    input_digest: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(12))
    phase: Mapped[str | None] = mapped_column(String(24))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    lease_count: Mapped[int] = mapped_column(Integer, default=0)
    repaired: Mapped[bool] = mapped_column(Boolean, default=False)
    checkpoint: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_message: Mapped[str | None] = mapped_column(String(512))
    error_retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    model_name: Mapped[str] = mapped_column(String(128))
    rules_version: Mapped[str] = mapped_column(String(32))


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="CASCADE"), unique=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    topic_key: Mapped[str] = mapped_column(String(64))
    topic_title: Mapped[str] = mapped_column(String(100))
    content: Mapped[dict] = mapped_column(JSONB)
    evidence: Mapped[list] = mapped_column(JSONB)
    ai_generated: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    rules_version: Mapped[str] = mapped_column(String(32))


class SavedTopic(Base):
    __tablename__ = "saved_topics"
    __table_args__ = (Index("ix_saved_topics_recent", "user_id", "updated_at", "topic_key"),)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    topic_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    scope: Mapped[str] = mapped_column(String(240))
    evidence: Mapped[list] = mapped_column(JSONB)
    evidence_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_attempt_start_key"),
        CheckConstraint("status IN ('ACTIVE', 'PASSED', 'ABANDONED')", name="ck_attempt_status"),
        CheckConstraint(
            "correct_count BETWEEN 0 AND answered_count AND answered_count BETWEEN 0 AND 20",
            name="ck_attempt_progress",
        ),
        Index("ix_attempts_quiz_status", "quiz_id", "status"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    quiz_id: Mapped[str] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE")
    answered_count: Mapped[int] = mapped_column(Integer, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rules_version: Mapped[str] = mapped_column(String(32))


class AttemptAnswer(Base):
    __tablename__ = "attempt_answers"
    __table_args__ = (
        UniqueConstraint("attempt_id", "question_index", name="uq_attempt_answer_index"),
    )

    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("attempts.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_index: Mapped[int] = mapped_column(Integer)
    receipt: Mapped[dict] = mapped_column(JSONB)


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (Index("ix_records_history", "user_id", "completed_at", "id"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    attempt_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    topic_title: Mapped[str] = mapped_column(String(100))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_questions: Mapped[int] = mapped_column(Integer)
    correct_count: Mapped[int] = mapped_column(Integer)
    takeaways: Mapped[list] = mapped_column(JSONB)
    takeaway_evidence: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    answers: Mapped[list] = mapped_column(JSONB)


class RecordSummary(Base):
    __tablename__ = "record_ai_summaries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')", name="ck_summary_status"
        ),
        Index("ix_summary_dispatch", "status", "next_run_at", "created_at"),
        Index("ix_summary_lease", "status", "lease_until"),
    )

    record_id: Mapped[str] = mapped_column(
        ForeignKey("records.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(12))
    content: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    lease_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_message: Mapped[str | None] = mapped_column(String(512))
    error_retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    model_name: Mapped[str] = mapped_column(String(128))
    rules_version: Mapped[str] = mapped_column(String(32))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_key: Mapped[str | None] = mapped_column(String(128))
    retry_keys: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))

    @property
    def pending_manual_retry(self) -> bool:
        return self.retry_key is not None and self.retry_count == 0
