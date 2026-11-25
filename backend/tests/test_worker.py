import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import db
from app.worker.ai import AIError

DATA = Path(__file__).resolve().parents[1] / "data" / "topics"


@pytest.fixture
def worker_module():
    return importlib.import_module("app.worker.queue")


@pytest.fixture
def quiz():
    return json.loads((DATA / "fixed-seasons.json").read_text(encoding="utf-8"))


class FakeAI:
    model = "deepseek-flash"

    def __init__(self, *, quiz=None):
        self.quiz = quiz
        self.calls = []
        self.verdicts = []
        self.generation = None
        self.summary = None

    def generate(self, topic):
        self.calls.append("generate")
        assert topic["topicKey"] == "seasons"
        return self.generation() if self.generation else self.quiz

    def review(self, topic, quiz):
        self.calls.append("review")
        if self.verdicts:
            return self.verdicts.pop(0)
        return {"accepted": True, "issues": []}

    def repair(self, topic, quiz, issues):
        self.calls.append("repair")
        assert issues
        return self.quiz

    def summarize(self, record):
        self.calls.append("summarize")
        return self.summary(record)


def queue_generation(database, settings, **overrides):
    now = db.utcnow()
    with database.begin() as session:
        user = db.User(app_id="test-wechat", openid=uuid4().hex)
        session.add(user)
        session.flush()
        job = db.GenerationJob(
            **{
                "user_id": user.id,
                "source": "TOPIC",
                "topic_key": "seasons",
                "topic_version": "v1",
                "input_digest": "0" * 64,
                "idempotency_key": uuid4().hex,
                "status": "QUEUED",
                "next_run_at": now,
                "created_at": now,
                "expires_at": now + timedelta(minutes=settings.job_minutes),
                "model_name": settings.ai_model,
                "rules_version": "quiz-v1",
                **overrides,
            }
        )
        session.add(job)
        session.flush()
        return job.id


def test_accepted_generated_quiz_uses_two_calls_and_publishes_once(
    worker_module, database, settings, quiz
):
    job_id = queue_generation(database, settings)
    ai = FakeAI(quiz=quiz)
    worker = worker_module.Worker(settings, database, ai)

    assert worker.process_once()
    assert not worker.process_once()
    assert ai.calls == ["generate", "review"]
    with database() as session:
        job = session.get(db.GenerationJob, job_id)
        stored = session.scalars(select(db.Quiz).where(db.Quiz.job_id == job_id)).one()
        assert (job.status, job.call_count, job.lease_count) == ("SUCCEEDED", 2, 1)
        assert job.lease_token is None
        assert stored.ai_generated
        assert len(stored.content["questions"]) == 20
        assert {item["evidenceId"] for item in stored.evidence} >= {"earth-tilt"}


@pytest.mark.parametrize("bad_shape", [False, True])
def test_worker_repairs_only_once_and_checks_the_repaired_quiz(
    worker_module, database, settings, quiz, bad_shape
):
    job_id = queue_generation(database, settings)
    ai = FakeAI(quiz=quiz)
    failing_review = {"accepted": False, "issues": ["seasons-01 含混"]}
    ai.verdicts = [failing_review]
    if not bad_shape:
        ai.verdicts.append(failing_review)
    else:
        ai.generation = lambda: {**quiz, "questions": quiz["questions"][:19]}

    worker_module.Worker(settings, database, ai).process_once()

    assert ai.calls == (
        ["generate", "repair", "review"]
        if bad_shape
        else ["generate", "review", "repair", "review"]
    )
    with database() as session:
        job = session.get(db.GenerationJob, job_id)
        assert job.repaired
        assert job.status == "FAILED"
        assert job.error_code == "GENERATION_QUALITY_FAILED"
        assert session.scalar(select(db.Quiz).where(db.Quiz.job_id == job_id)) is None


def test_locked_job_is_skipped_and_a_second_available_job_runs(
    worker_module, database, settings, quiz
):
    locked = queue_generation(database, settings)
    available = queue_generation(database, settings)
    ai = FakeAI(quiz=quiz)

    with database.begin() as locking:
        locking.get(db.GenerationJob, locked, with_for_update=True)
        assert worker_module.Worker(settings, database, ai).process_once()

    with database() as session:
        assert session.get(db.GenerationJob, locked).status == "QUEUED"
        assert session.get(db.GenerationJob, available).status == "SUCCEEDED"


def test_expired_lease_can_be_reclaimed_and_the_old_response_cannot_publish(
    worker_module, database, settings, quiz
):
    job_id = queue_generation(database, settings)
    old_ai = FakeAI(quiz=quiz)
    new_ai = FakeAI(quiz={**quiz, "title": "四季成因巩固"})
    started = Event()
    release = Event()

    def delayed_generation():
        started.set()
        assert release.wait(5)
        return quiz

    old_ai.generation = delayed_generation

    def run_old():
        return worker_module.Worker(settings, database, old_ai).process_once()

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(run_old)
        try:
            assert started.wait(5)
            with database.begin() as session:
                job = session.get(db.GenerationJob, job_id, with_for_update=True)
                assert job.call_count == 1
                job.lease_until = db.utcnow() - timedelta(seconds=1)
            assert worker_module.Worker(settings, database, new_ai).process_once()
        finally:
            release.set()
        assert pending.result(timeout=5)

    assert old_ai.calls == ["generate"]
    assert new_ai.calls == ["generate", "review"]
    with database() as session:
        job = session.get(db.GenerationJob, job_id)
        stored = session.scalars(select(db.Quiz).where(db.Quiz.job_id == job_id)).one()
        assert (job.status, job.call_count, job.lease_count) == ("SUCCEEDED", 4, 2)
        assert stored.content["title"] == "四季成因巩固"


def test_remaining_generation_budget_cannot_start_an_unreviewable_quiz(
    worker_module, database, settings, quiz
):
    job_id = queue_generation(database, settings, call_count=settings.max_generation_calls - 1)
    ai = FakeAI(quiz=quiz)

    assert worker_module.Worker(settings, database, ai).process_once()
    assert ai.calls == []
    with database() as session:
        job = session.get(db.GenerationJob, job_id)
        assert job.status == "FAILED"
        assert job.call_count == settings.max_generation_calls - 1
        assert job.error_code == "GENERATION_QUALITY_FAILED"


def test_transient_failure_requeues_and_reuses_the_same_budget(
    worker_module, database, settings, quiz
):
    job_id = queue_generation(database, settings)
    ai = FakeAI(quiz=quiz)

    def unavailable():
        raise AIError("PROVIDER_UNAVAILABLE", "模型服务暂时不可用。", True)

    ai.generation = unavailable
    worker = worker_module.Worker(settings, database, ai)
    assert worker.process_once()
    assert not worker.process_once()
    with database.begin() as session:
        job = session.get(db.GenerationJob, job_id)
        assert (job.status, job.call_count, job.error_code) == ("QUEUED", 1, "PROVIDER_UNAVAILABLE")
        assert job.next_run_at > db.utcnow()
        assert job.lease_token is None
        job.next_run_at = db.utcnow() - timedelta(seconds=1)

    ai.generation = None
    assert worker.process_once()
    with database() as session:
        job = session.get(db.GenerationJob, job_id)
        assert (job.status, job.call_count, job.lease_count) == ("SUCCEEDED", 3, 2)


def test_worker_fails_if_the_bound_source_version_is_missing(
    worker_module, database, settings, quiz
):
    job_id = queue_generation(database, settings, topic_version="missing")
    ai = FakeAI(quiz=quiz)

    assert worker_module.Worker(settings, database, ai).process_once()
    assert ai.calls == []
    with database() as session:
        job = session.get(db.GenerationJob, job_id)
        assert (job.status, job.call_count, job.error_code) == ("FAILED", 0, "SOURCE_INSUFFICIENT")


def queue_summary(database, settings, quiz, *, retry_count=0, call_count=0):
    now = db.utcnow()
    topic = json.loads((DATA / "seasons.v1.json").read_text(encoding="utf-8"))
    evidence = {item["evidenceId"]: item for item in topic["evidence"]}
    answers = [
        {
            "question": {key: question[key] for key in ("questionId", "stem", "type", "options")},
            "explanation": question["explanation"],
            "selectedOptionIds": question["correctOptionIds"],
            "correctOptionIds": question["correctOptionIds"],
            "isCorrect": True,
            "evidence": [evidence[key] for key in question["evidenceIds"]],
        }
        for question in quiz["questions"]
    ]
    with database.begin() as session:
        user = db.User(app_id="test-wechat", openid=uuid4().hex)
        session.add(user)
        session.flush()
        record = db.Record(
            attempt_id=db.new_id(),
            user_id=user.id,
            topic_title=topic["title"],
            completed_at=now,
            total_questions=20,
            correct_count=20,
            takeaways=quiz["takeaways"],
            answers=answers,
        )
        session.add(record)
        session.flush()
        session.add(
            db.RecordSummary(
                record_id=record.id,
                status="QUEUED",
                next_run_at=now,
                created_at=now,
                expires_at=now + timedelta(minutes=settings.job_minutes),
                model_name=settings.ai_model,
                rules_version="summary-v1",
                call_count=call_count,
                retry_count=retry_count,
            )
        )
        return record.id


def test_summary_automatic_retry_preserves_one_call_for_manual_retry(
    worker_module, database, settings, quiz
):
    record_id = queue_summary(database, settings, quiz, call_count=settings.max_summary_calls - 2)
    ai = FakeAI()
    worker = worker_module.Worker(settings, database, ai)

    def timeout(record):
        assert set(record) == {"topicTitle", "takeaways", "takeawayEvidence", "answers"}
        raise AIError("PROVIDER_TIMEOUT", "模型服务响应超时。", True)

    ai.summary = timeout
    assert worker.process_once()
    with database.begin() as session:
        summary = session.get(db.RecordSummary, record_id)
        assert (summary.status, summary.error_code) == ("FAILED", "PROVIDER_TIMEOUT")
        assert summary.error_retryable
        assert summary.call_count == settings.max_summary_calls - 1
        summary.status = "QUEUED"
        summary.retry_count = 1
        summary.next_run_at = db.utcnow()
    assert worker.process_once()
    with database() as session:
        summary = session.get(db.RecordSummary, record_id)
        assert summary.status == "FAILED"
        assert not summary.error_retryable
        assert summary.call_count == settings.max_summary_calls


def test_summary_with_existing_citations_finishes_without_mutating_record(
    worker_module, database, settings, quiz
):
    record_id = queue_summary(database, settings, quiz)
    ai = FakeAI()
    item = {"text": "复习地轴倾斜。", "questionIds": ["seasons-01"], "evidenceIds": ["earth-tilt"]}
    result = {"knowledgeReview": [item], "mistakeNotes": [], "reviewSuggestions": [item]}
    ai.summary = lambda record: result

    assert worker_module.Worker(settings, database, ai).process_once()
    assert ai.calls == ["summarize"]
    with database() as session:
        summary = session.get(db.RecordSummary, record_id)
        assert summary.status == "SUCCEEDED"
        assert summary.content == result
        assert session.get(db.Record, record_id).correct_count == 20


def test_cleanup_expires_queued_tasks_and_preserves_a_live_lease(worker_module, database, settings):
    dead = queue_generation(database, settings, expires_at=db.utcnow() - timedelta(seconds=1))
    live = queue_generation(
        database,
        settings,
        status="RUNNING",
        lease_token=db.new_id(),
        lease_until=db.utcnow() + timedelta(minutes=3),
    )

    worker_module.Worker(settings, database, FakeAI()).cleanup()
    with database() as session:
        dead_job = session.get(db.GenerationJob, dead)
        assert (dead_job.status, dead_job.error_code) == ("FAILED", "PROVIDER_TIMEOUT")
        live_job = session.get(db.GenerationJob, live)
        assert live_job.status == "RUNNING"
        assert live_job.lease_token


def test_summary_day_quota_binds_real_model_calls_and_keeps_basic_result(
    worker_module, database, settings, quiz
):
    constrained = settings.model_copy(update={"global_daily_summary_limit": 1})
    first = queue_summary(database, constrained, quiz)
    second = queue_summary(database, constrained, quiz)
    ai = FakeAI()
    item = {"text": "复习地轴倾斜。", "questionIds": ["seasons-01"], "evidenceIds": ["earth-tilt"]}
    ai.summary = lambda record: {
        "knowledgeReview": [item],
        "mistakeNotes": [],
        "reviewSuggestions": [item],
    }
    worker = worker_module.Worker(constrained, database, ai)

    assert worker.process_once() and worker.process_once()
    assert ai.calls == ["summarize"]
    with database() as session:
        assert session.get(db.RecordSummary, first).status == "SUCCEEDED"
        blocked = session.get(db.RecordSummary, second)
        assert (blocked.status, blocked.error_code, blocked.call_count) == (
            "FAILED",
            "RATE_LIMITED",
            0,
        )
        assert blocked.error_retryable
        assert session.get(db.Record, second).correct_count == 20


def test_review_timeout_resumes_repaired_candidate_with_one_call_left(
    worker_module, database, settings, quiz
):
    settings = settings.model_copy(update={"max_generation_calls": 5})
    job_id = queue_generation(database, settings)

    class ReviewTimeout(FakeAI):
        def review(self, topic, candidate):
            self.calls.append(("review", candidate["title"]))
            if candidate["title"] != "修正题组":
                return {"accepted": False, "issues": ["需要修正"]}
            if self.calls.count(("review", "修正题组")) == 1:
                raise AIError("PROVIDER_TIMEOUT", "核验超时", retryable=True)
            return {"accepted": True, "issues": []}

        def repair(self, topic, candidate, issues):
            self.calls.append("repair")
            return {**quiz, "title": "修正题组"}

    model = ReviewTimeout(quiz=quiz)
    assert worker_module.Worker(settings, database, model).process_once()
    with database.begin() as session:
        job = session.get(db.GenerationJob, job_id)
        assert job.status == "QUEUED"
        assert job.call_count == 4
        job.next_run_at = db.utcnow()

    assert worker_module.Worker(settings, database, model).process_once()
    with database() as session:
        job = session.get(db.GenerationJob, job_id)
        assert (job.status, job.call_count, job.repaired) == ("SUCCEEDED", 5, True)
        assert job.checkpoint is None
        stored = session.scalar(select(db.Quiz).where(db.Quiz.job_id == job_id))
        assert stored.content["title"] == "修正题组"
    assert model.calls == [
        "generate",
        ("review", quiz["title"]),
        "repair",
        ("review", "修正题组"),
        ("review", "修正题组"),
    ]
