"""PostgreSQL task leases and durable model call budgets."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic

from pydantic import ValidationError
from sqlalchemy import and_, delete, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
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
    SavedTopic,
    SessionRow,
    new_id,
    utcnow,
)
from app.domain.quizzes import validate_quiz
from app.domain.summaries import validate_summary
from app.domain.topics import (
    CURRENT_VERSIONS,
    get_topic_version,
    live_topic_key,
    match_topic,
    normalize,
)
from app.quota import QuotaReached, reserve_summary_quota
from app.worker.ai import AIError, ChatAI
from app.worker.search import MIN_PASSAGES, TavilySearch

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


def _issue_label(location: tuple[int | str, ...], raw: dict) -> str:
    if not location:
        return "题组"
    if location[0] == "questions" and len(location) > 1 and isinstance(location[1], int):
        questions = raw.get("questions") if isinstance(raw, dict) else None
        if isinstance(questions, list) and 0 <= location[1] < len(questions):
            question = questions[location[1]]
            if isinstance(question, dict) and isinstance(question.get("questionId"), str):
                return question["questionId"]
        return f"questions.{location[1]}"
    if location[0] == "takeaways":
        return "知识要点"
    if location[0] == "title":
        return "标题"
    return ".".join(map(str, location))


def _validation_issues(error: ValueError, raw: dict) -> list[str]:
    if isinstance(error, ValidationError):
        return [
            f"{_issue_label(item['loc'], raw)}: {item['msg']}"
            for item in error.errors(include_url=False, include_context=False, include_input=False)
        ]
    return [str(error)[:300] or "题组结构无效"]


def _clear_lease(row: Task) -> None:
    row.lease_token = None
    row.lease_until = None
    if isinstance(row, GenerationJob):
        row.phase = None
        if row.status in {"SUCCEEDED", "FAILED"}:
            row.checkpoint = None
            row.topic_input = None


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
        ai: ChatAI,
        search: TavilySearch | None = None,
    ):
        self.settings = settings
        self.sessions = sessions
        self.ai = ai
        self.search = search or TavilySearch(
            settings.tavily_api_key.get_secret_value(), settings.search_timeout
        )
        self.lease_duration = timedelta(
            seconds=max(settings.model_timeout, 2 * settings.search_timeout)
            + max(10, 2 * settings.worker_poll_seconds)
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
            unpaid_retry = isinstance(row, RecordSummary) and row.pending_manual_retry
            if (
                row.status == "RUNNING"
                and not unpaid_retry
                and row.call_count < self._call_limit(row)
            ):
                row.call_count += 1
            row.status = "RUNNING"
            row.lease_count += 1
            row.lease_token = new_id()
            row.lease_until = min(now + self.lease_duration, row.expires_at)
            if isinstance(row, GenerationJob):
                row.phase = None
                return _Claim(kind, row.id, row.lease_token)
            return _Claim(kind, row.record_id, row.lease_token)

    def _call_limit(self, row: Task) -> int:
        if isinstance(row, GenerationJob):
            return self.settings.max_generation_calls
        keep_manual_slot = row.retry_count == 0 and not row.pending_manual_retry
        return self.settings.max_summary_calls - int(keep_manual_slot)

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
        phase: str = "GENERATING",
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
                row.phase = phase
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
                if row.pending_manual_retry:
                    row.retry_count += 1
            row.model_name = getattr(self.ai, "model", self.settings.ai_model)
            row.call_count += 1
            row.lease_until = min(utcnow() + self.lease_duration, row.expires_at)
        return operation(*args)

    def _generate(self, claim: _Claim) -> None:
        with self.sessions.begin() as session:
            row = self._owned(session, claim)
            assert isinstance(row, GenerationJob)
            topic_key, topic_version = row.topic_key, row.topic_version
            checkpoint = row.checkpoint or {}
            topic_input = row.topic_input
            repair_started = row.repaired
        if "topic" in checkpoint:
            topic = checkpoint["topic"]
        elif topic_input:
            topic = self._live_topic(claim, topic_input, checkpoint, topic_key)
        else:
            try:
                self._set_reading_sources(claim)
                topic = get_topic_version(topic_key, topic_version)
            except ValueError:
                raise AIError(
                    "SOURCE_INSUFFICIENT", "当前主题的资料无法支持出题。", False
                ) from None

        if "candidate" not in checkpoint:
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
                    topic_key=topic["topicKey"],
                    topic_title=topic["title"],
                    content=quiz,
                    evidence=evidence,
                    ai_generated=True,
                    created_at=now,
                    expires_at=now + timedelta(hours=self.settings.quiz_hours),
                    rules_version=row.rules_version,
                )
            )
            row.topic_key = topic["topicKey"]
            row.topic_version = topic["version"]
            if "scope" in topic and topic["topicKey"] not in CURRENT_VERSIONS:
                self._save_topic(session, row.user_id, topic, now)
            _succeed(row)

    def _save_topic(self, session: Session, user_id: str, topic: dict, now: datetime) -> None:
        evidence_at = datetime.fromisoformat(topic["evidenceAt"])
        statement = insert(SavedTopic).values(
            user_id=user_id,
            topic_key=topic["topicKey"],
            title=topic["title"],
            scope=topic["scope"],
            evidence=topic["evidence"],
            evidence_at=evidence_at,
            updated_at=now,
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[SavedTopic.user_id, SavedTopic.topic_key],
                set_={
                    "title": statement.excluded.title,
                    "scope": statement.excluded.scope,
                    "evidence": statement.excluded.evidence,
                    "evidence_at": statement.excluded.evidence_at,
                    "updated_at": statement.excluded.updated_at,
                },
                where=statement.excluded.evidence_at >= SavedTopic.evidence_at,
            )
        )

    def _set_reading_sources(self, claim: _Claim) -> None:
        with self.sessions.begin() as session:
            row = self._owned(session, claim, lock=True)
            assert isinstance(row, GenerationJob)
            row.phase = "READING_SOURCES"

    def _live_topic(
        self, claim: _Claim, topic_input: str, checkpoint: dict, saved_key: str | None
    ) -> dict:
        intent = checkpoint.get("intent")
        if intent is None:
            scope = checkpoint.get("savedScope")
            model_input = f"主题：{topic_input}\n学习范围：{scope}" if scope else topic_input
            intent = self._model_call(
                claim,
                self.ai.resolve_topic,
                model_input,
                slots_needed=4,
                phase="RESOLVING_TOPIC",
            )
            if intent["needsClarification"] or not normalize(intent["title"]):
                raise AIError(
                    "TOPIC_NEEDS_CLARIFICATION", "请补充想了解的概念或缩小主题范围。", False
                )
            with self.sessions.begin() as session:
                row = self._owned(session, claim, lock=True)
                assert isinstance(row, GenerationJob)
                if saved_key and row.topic_title and scope:
                    intent = {**intent, "title": row.topic_title, "scope": scope}
                row.topic_title = intent["title"]
                row.checkpoint = {**(row.checkpoint or {}), "intent": intent}

        passages = checkpoint.get("passages")
        evidence_at = checkpoint.get("evidenceAt")
        if passages is None:
            passages = self._find_passages(claim, list(dict.fromkeys(intent["queries"])))
            evidence_at = utcnow().isoformat()
            self._update_checkpoint(claim, passages=passages, evidenceAt=evidence_at)

        insufficient = AIError(
            "SOURCE_INSUFFICIENT",
            "找到的可信资料不足以生成 20 道题。请缩小主题范围或换个主题。",
            False,
        )
        if len(passages) < MIN_PASSAGES:
            raise insufficient
        selection = self._model_call(
            claim,
            self.ai.select_sources,
            intent,
            passages,
            slots_needed=3,
            phase="READING_SOURCES",
        )
        ids = selection["evidenceIds"]
        selected = set(ids)
        if len(selected) != len(ids) or not selected <= {item["evidenceId"] for item in passages}:
            raise AIError("PROVIDER_INVALID_OUTPUT", "资料核对未完成，请稍后重试。", True)
        evidence = [item for item in passages if item["evidenceId"] in selected]
        if len(evidence) < MIN_PASSAGES or len({item["sourceUrl"] for item in evidence}) < 2:
            raise insufficient
        try:
            example = match_topic(intent["title"]) if not saved_key else None
        except ValueError:
            example = None
        topic = {
            "topicKey": saved_key
            or (example.topicKey if example else live_topic_key(intent["title"])),
            "title": intent["title"],
            "scope": intent["scope"],
            "version": "web-v1",
            "evidence": evidence,
            "evidenceAt": evidence_at,
        }
        self._update_checkpoint(claim, topic=topic)
        return topic

    def _find_passages(self, claim: _Claim, queries: list[str]) -> list[dict]:
        with self.sessions.begin() as session:
            row = self._owned(session, claim, lock=True)
            assert isinstance(row, GenerationJob)
            if row.search_count >= 2:
                raise AIError("SEARCH_UNAVAILABLE", "资料检索暂时无法完成。", False)
            row.search_count += 1
            row.phase = "READING_SOURCES"
            row.lease_until = min(utcnow() + self.lease_duration, row.expires_at)
        started = monotonic()
        try:
            return self.search.passages(queries)
        finally:
            logger.info(
                "Search task=%s duration_ms=%d", claim.row_id, (monotonic() - started) * 1000
            )

    def _update_checkpoint(self, claim: _Claim, **values) -> None:
        with self.sessions.begin() as session:
            row = self._owned(session, claim, lock=True)
            assert isinstance(row, GenerationJob)
            row.checkpoint = {**(row.checkpoint or {}), **values}

    def _save_candidate(self, claim: _Claim, candidate: dict, *, corrected: bool) -> None:
        self._update_checkpoint(claim, candidate=candidate, corrected=corrected)

    def _validate_candidate(
        self, claim: _Claim, topic: dict, raw: dict
    ) -> tuple[dict | None, list[str]]:
        try:
            quiz = validate_quiz(raw, topic).model_dump(mode="json", by_alias=True)
        except ValueError as error:
            return None, _validation_issues(error, raw)
        verdict = self._model_call(claim, self.ai.review, topic, quiz, phase="VERIFYING")
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
                "takeawayEvidence": record.takeaway_evidence,
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
                    checkpoint = row.checkpoint or {}
                    needed = 1 if "candidate" in checkpoint else 2
                    if row.repaired and not checkpoint.get("corrected"):
                        # A dispatched correction is never sent to the provider a second time.
                        can_resume = False
                    if error.code == "SEARCH_UNAVAILABLE" and row.search_count >= 2:
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
            session.execute(
                delete(Quiz).where(
                    Quiz.expires_at <= now - timedelta(hours=24),
                    Quiz.topic_key.not_in(CURRENT_VERSIONS),
                    ~exists().where(
                        SavedTopic.user_id == Quiz.user_id, SavedTopic.topic_key == Quiz.topic_key
                    ),
                )
            )
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
