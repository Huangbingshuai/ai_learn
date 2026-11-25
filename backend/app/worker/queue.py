"""PostgreSQL task leases and durable DeepSeek call budgets."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import and_, delete, exists, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import (
    Attempt,
    GenerationJob,
    Quiz,
    RateLimit,
    Record,
    RecordSummary,
    SessionRow,
    new_id,
    utcnow,
)
from app.domain.quizzes import validate_quiz
from app.domain.summaries import validate_summary
from app.domain.topics import get_topic_version
from app.quota import QuotaReached, reserve_summary_quota
from app.worker.ai import AIError, DeepSeekAI

logger = logging.getLogger(__name__)

Task = GenerationJob | RecordSummary
TaskType = type[GenerationJob] | type[RecordSummary]


@dataclass(frozen=True)
class _Claim:
    kind: TaskType
    row_id: str
    token: str


class _LeaseLost(Exception):
    pass


def _quality_error() -> AIError:
    return AIError("GENERATION_QUALITY_FAILED", "题组未通过质量校验，请重新生成或更换主题。", True)


def _deadline_error() -> AIError:
    return AIError("PROVIDER_TIMEOUT", "任务处理超时。", True)


def _validation_issues(error: ValueError) -> list[str]:
    if isinstance(error, ValidationError):
        return [
            f"{'.'.join(map(str, item['loc'])) or 'quiz'}: {item['msg']}"
            for item in error.errors(include_url=False, include_context=False, include_input=False)[
                :12
            ]
        ]
    return [str(error)[:300] or "题组结构无效"]


def _clear_lease(row: Task) -> None:
    row.lease_token = None
    row.lease_until = None
    if isinstance(row, GenerationJob):
        row.phase = None
        if row.status in {"SUCCEEDED", "FAILED"}:
            row.checkpoint = None


def _succeed(row: Task) -> None:
    row.status = "SUCCEEDED"
    row.error_code = None
    row.error_message = None
    row.error_retryable = False
    _clear_lease(row)


class Worker:
    def __init__(
        self,
        settings: Settings,
        sessions: sessionmaker[Session],
        ai: DeepSeekAI,
    ):
        self.settings = settings
        self.sessions = sessions
        self.ai = ai
        self.lease_duration = timedelta(
            seconds=settings.model_timeout + max(10, 2 * settings.worker_poll_seconds)
        )
        self._next_kind: TaskType = GenerationJob

    def process_once(self) -> bool:
        """Process one due task, returning whether any task was claimed."""
        preferred = self._next_kind
        other = RecordSummary if preferred is GenerationJob else GenerationJob
        claim = self._claim(preferred) or self._claim(other)
        if claim is None:
            return False
        self._next_kind = RecordSummary if claim.kind is GenerationJob else GenerationJob

        try:
            if claim.kind is GenerationJob:
                self._generate(claim)
            else:
                self._summarize(claim)
        except _LeaseLost:
            logger.info("Worker discarded expired lease task=%s", claim.row_id)
        except AIError as error:
            self._handle_error(claim, error)
        except SQLAlchemyError:
            raise
        except Exception as error:  # noqa: BLE001 - contain unexpected failures to this task
            logger.error(
                "Worker task failed task=%s exception_type=%s", claim.row_id, type(error).__name__
            )
            self._handle_error(claim, AIError("WORKER_ERROR", "任务暂时无法处理。", False))
        return True

    def _claim(self, kind: TaskType) -> _Claim | None:
        now = utcnow()
        with self.sessions.begin() as session:
            row = session.scalar(
                select(kind)
                .where(
                    kind.expires_at > now,
                    or_(
                        and_(kind.status == "QUEUED", kind.next_run_at <= now),
                        and_(
                            kind.status == "RUNNING",
                            or_(kind.lease_until.is_(None), kind.lease_until <= now),
                        ),
                    ),
                )
                .order_by(kind.next_run_at, kind.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            if row.status == "RUNNING" and row.call_count < self._call_limit(row):
                row.call_count += 1
            row.status = "RUNNING"
            row.lease_count += 1
            row.lease_token = new_id()
            row.lease_until = min(now + self.lease_duration, row.expires_at)
            if isinstance(row, GenerationJob):
                row.phase = "READING_SOURCES"
                return _Claim(kind, row.id, row.lease_token)
            return _Claim(kind, row.record_id, row.lease_token)

    def _call_limit(self, row: Task) -> int:
        if isinstance(row, GenerationJob):
            return self.settings.max_generation_calls
        return self.settings.max_summary_calls - int(row.retry_count == 0)

    def _owned(self, session: Session, claim: _Claim, *, lock: bool = False) -> Task:
        row = session.get(claim.kind, claim.row_id, with_for_update=lock)
        now = utcnow()
        if (
            row is None
            or row.status != "RUNNING"
            or row.lease_token != claim.token
            or row.lease_until is None
            or row.lease_until <= now
            or row.expires_at <= now
        ):
            raise _LeaseLost()
        return row

    def _model_call(
        self,
        claim: _Claim,
        operation: Callable[..., dict],
        *args: object,
        slots_needed: int = 1,
        repair: bool = False,
    ) -> dict:
        with self.sessions.begin() as session:
            row = self._owned(session, claim, lock=True)
            if self._call_limit(row) - row.call_count < slots_needed:
                if isinstance(row, GenerationJob):
                    raise _quality_error()
                error_code = row.error_code or "PROVIDER_TIMEOUT"
                error_message = row.error_message or "总结任务的处理次数已用完。"
                raise AIError(error_code, error_message, True)
            if isinstance(row, GenerationJob):
                if repair and row.repaired:
                    raise _quality_error()
                row.repaired = row.repaired or repair
                row.phase = "GENERATING"
            else:
                user_id = session.scalar(select(Record.user_id).where(Record.id == row.record_id))
                if user_id is None:
                    raise _LeaseLost()
                try:
                    reserve_summary_quota(session, user_id, utcnow(), self.settings)
                except QuotaReached:
                    raise AIError(
                        "RATE_LIMITED", "今天的总结用量已达上限，基础结果仍可查看。", True
                    ) from None
            row.model_name = self.settings.deepseek_model
            row.call_count += 1
            row.lease_until = min(utcnow() + self.lease_duration, row.expires_at)
        return operation(*args)

    def _generate(self, claim: _Claim) -> None:
        with self.sessions.begin() as session:
            row = self._owned(session, claim)
            assert isinstance(row, GenerationJob)
            topic_key, topic_version = row.topic_key, row.topic_version
            checkpoint = row.checkpoint
            repair_started = row.repaired
        try:
            topic = get_topic_version(topic_key, topic_version)
        except ValueError:
            raise AIError("SOURCE_INSUFFICIENT", "当前主题的资料无法支持出题。", False) from None

        if checkpoint is None:
            raw = self._model_call(claim, self.ai.generate, topic, slots_needed=2)
            self._save_candidate(claim, raw, corrected=False)
        else:
            if repair_started and not checkpoint["corrected"]:
                raise _deadline_error()
            raw = checkpoint["candidate"]

        quiz, issues = self._validate_candidate(claim, topic, raw)
        if issues and not repair_started:
            repaired = self._model_call(
                claim,
                self.ai.repair,
                topic,
                quiz if quiz is not None else raw,
                issues,
                slots_needed=2,
                repair=True,
            )
            self._save_candidate(claim, repaired, corrected=True)
            quiz, issues = self._validate_candidate(claim, topic, repaired)
        if issues or quiz is None:
            raise _quality_error()

        cited_ids = {
            evidence_id
            for item in [*quiz["takeaways"], *quiz["questions"]]
            for evidence_id in item["evidenceIds"]
        }
        evidence = [item for item in topic["evidence"] if item["evidenceId"] in cited_ids]
        with self.sessions.begin() as session:
            row = self._owned(session, claim, lock=True)
            assert isinstance(row, GenerationJob)
            now = utcnow()
            session.add(
                Quiz(
                    job_id=row.id,
                    user_id=row.user_id,
                    topic_key=row.topic_key,
                    topic_title=topic["title"],
                    content=quiz,
                    evidence=evidence,
                    ai_generated=True,
                    created_at=now,
                    expires_at=now + timedelta(hours=self.settings.quiz_hours),
                    rules_version=row.rules_version,
                )
            )
            _succeed(row)

    def _save_candidate(self, claim: _Claim, candidate: dict, *, corrected: bool) -> None:
        with self.sessions.begin() as session:
            row = self._owned(session, claim, lock=True)
            assert isinstance(row, GenerationJob)
            row.checkpoint = {"candidate": candidate, "corrected": corrected}

    def _validate_candidate(
        self, claim: _Claim, topic: dict, raw: dict
    ) -> tuple[dict | None, list[str]]:
        try:
            quiz = validate_quiz(raw, topic).model_dump(mode="json", by_alias=True)
        except ValueError as error:
            return None, _validation_issues(error)
        verdict = self._model_call(claim, self.ai.review, topic, quiz)
        if not verdict["accepted"]:
            return quiz, verdict["issues"] or ["事实核验未通过"]
        return quiz, []

    def _summarize(self, claim: _Claim) -> None:
        with self.sessions.begin() as session:
            self._owned(session, claim)
            record = session.get(Record, claim.row_id)
            if record is None:
                raise _LeaseLost()
            snapshot = {
                "topicTitle": record.topic_title,
                "takeaways": record.takeaways,
                "answers": record.answers,
            }
        raw = self._model_call(claim, self.ai.summarize, snapshot)
        try:
            summary = validate_summary(raw, snapshot).model_dump(mode="json", by_alias=True)
        except ValueError:
            raise AIError("SUMMARY_QUALITY_FAILED", "学习总结未通过校验。", True) from None
        with self.sessions.begin() as session:
            row = self._owned(session, claim, lock=True)
            assert isinstance(row, RecordSummary)
            row.content = summary
            _succeed(row)

    def _handle_error(self, claim: _Claim, error: AIError) -> None:
        try:
            with self.sessions.begin() as session:
                row = self._owned(session, claim, lock=True)
                needed = 1
                can_resume = True
                if isinstance(row, GenerationJob):
                    checkpoint = row.checkpoint
                    needed = 2 if checkpoint is None else 1
                    if checkpoint is not None and row.repaired and not checkpoint["corrected"]:
                        # A dispatched correction is never sent to the provider a second time.
                        can_resume = False
                delay = timedelta(seconds=2 ** min(max(row.call_count, 1), 5))
                next_run_at = utcnow() + delay
                schedule = (
                    error.retryable
                    and can_resume
                    and error.code not in {"GENERATION_QUALITY_FAILED", "RATE_LIMITED"}
                    and row.call_count + needed <= self._call_limit(row)
                    and next_run_at < row.expires_at
                )
                if schedule:
                    row.status = "QUEUED"
                    row.next_run_at = next_run_at
                else:
                    row.status = "FAILED"
                    if isinstance(row, GenerationJob) and error.code == "PROVIDER_INVALID_OUTPUT":
                        error = _quality_error()
                row.error_code = error.code
                row.error_message = error.message
                row.error_retryable = error.retryable and (
                    isinstance(row, GenerationJob)
                    or (row.retry_count == 0 and row.call_count < self.settings.max_summary_calls)
                )
                _clear_lease(row)
        except _LeaseLost:
            return
        logger.info(
            "Worker task ended task=%s status=%s code=%s", claim.row_id, row.status, error.code
        )

    def cleanup(self) -> None:
        """Expire tasks against their deadlines and remove short-lived data."""
        now = utcnow()
        with self.sessions.begin() as session:
            self._expire_tasks(session, GenerationJob, now)
            self._expire_tasks(session, RecordSummary, now)
            session.execute(
                update(Attempt)
                .where(
                    Attempt.status == "ACTIVE",
                    Attempt.quiz_id.in_(select(Quiz.id).where(Quiz.expires_at <= now)),
                )
                .values(status="ABANDONED")
            )
            session.execute(delete(Quiz).where(Quiz.expires_at <= now - timedelta(hours=24)))
            session.execute(
                delete(GenerationJob).where(
                    GenerationJob.status.in_(("SUCCEEDED", "FAILED")),
                    GenerationJob.created_at <= now - timedelta(hours=48),
                    ~exists().where(Quiz.job_id == GenerationJob.id),
                )
            )
            session.execute(delete(SessionRow).where(SessionRow.expires_at <= now))
            session.execute(delete(RateLimit).where(RateLimit.expires_at <= now))

    def _expire_tasks(self, session: Session, kind: TaskType, now: datetime) -> None:
        rows = session.scalars(
            select(kind)
            .where(
                kind.expires_at <= now,
                or_(
                    kind.status == "QUEUED",
                    and_(
                        kind.status == "RUNNING",
                        or_(kind.lease_until.is_(None), kind.lease_until <= now),
                    ),
                ),
            )
            .with_for_update(skip_locked=True)
        )
        deadline = _deadline_error()
        for row in rows:
            row.status = "FAILED"
            row.error_code = deadline.code
            row.error_message = deadline.message
            row.error_retryable = isinstance(row, GenerationJob) or (
                row.retry_count == 0 and row.call_count < self.settings.max_summary_calls
            )
            _clear_lease(row)
