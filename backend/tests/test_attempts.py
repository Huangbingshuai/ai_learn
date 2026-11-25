import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

DATA = Path(__file__).resolve().parents[1] / "data" / "topics"


@pytest.fixture
def fixed_data():
    return json.loads((DATA / "fixed-seasons.json").read_text())


@pytest.fixture
def play(client, login):
    headers = login()

    def with_key(key):
        return {**headers, "Idempotency-Key": key}

    fixed = client.post("/v1/dev/quizzes/fixed", headers=with_key("fixed"))
    assert fixed.status_code == 201, fixed.text
    quiz_id = fixed.json()["quizId"]
    attempt = client.post("/v1/attempts", json={"quizId": quiz_id}, headers=with_key("start"))
    assert attempt.status_code == 201, attempt.text
    return headers, quiz_id, attempt.json()


def test_attempt_projects_one_question_and_is_idempotent(client, play, login):
    headers, quiz_id, attempt = play
    assert attempt["answeredCount"] == 0
    assert attempt["totalQuestions"] == 20
    assert attempt["status"] == "ACTIVE"
    assert set(attempt["question"]) == {
        "questionId",
        "type",
        "stem",
        "options",
        "index",
        "aiGenerated",
    }
    assert attempt["question"]["index"] == 0
    assert attempt["question"]["aiGenerated"] is False
    start = client.post(
        "/v1/attempts", json={"quizId": quiz_id}, headers={**headers, "Idempotency-Key": "start"}
    )
    assert start.json()["attemptId"] == attempt["attemptId"]
    same = client.post("/v1/dev/quizzes/fixed", headers={**headers, "Idempotency-Key": "fixed"})
    assert same.json()["quizId"] == quiz_id
    other = login("device-999999")
    assert client.get(f"/v1/attempts/{attempt['attemptId']}", headers=other).status_code == 404
    assert (
        client.post(
            "/v1/attempts", json={"quizId": quiz_id}, headers={**other, "Idempotency-Key": "x"}
        ).status_code
        == 404
    )


def test_expired_quiz_cannot_be_reopened(client, play, database):
    from app.db import Quiz, utcnow

    headers, quiz_id, attempt = play
    with database.begin() as db:
        db.get(Quiz, quiz_id).expires_at = utcnow() - timedelta(seconds=1)
    response = client.post(
        "/v1/attempts", json={"quizId": quiz_id}, headers={**headers, "Idempotency-Key": "restart"}
    )
    assert (response.status_code, response.json()["code"]) == (409, "QUIZ_EXPIRED")
    current = client.get(f"/v1/attempts/{attempt['attemptId']}", headers=headers).json()
    assert current["status"] == "ABANDONED"
    assert current["question"] is None


def choose(client, headers, attempt_id, question_id, ids):
    return client.put(
        f"/v1/attempts/{attempt_id}/answers/{question_id}",
        json={"selectedOptionIds": ids},
        headers=headers,
    )


def finish(client, headers, attempt_id, fixed_data, *, wrong=False, skip_last=False):
    results = []
    items = fixed_data["questions"][:-1] if skip_last else fixed_data["questions"]
    for index, item in enumerate(items):
        correct = item["correctOptionIds"]
        ids = (
            [next(opt["optionId"] for opt in item["options"] if opt["optionId"] not in correct)]
            if wrong and index == 0
            else correct
        )
        result = choose(client, headers, attempt_id, item["questionId"], ids)
        assert result.status_code == 200, result.text
        results.append(result.json())
    return results


def test_first_submission_is_locked_and_returns_sources(client, play, fixed_data):
    headers, _, attempt = play
    question = fixed_data["questions"][0]
    aid = attempt["attemptId"]
    qid = question["questionId"]
    assert (
        choose(client, headers, aid, fixed_data["questions"][1]["questionId"], ["o1"]).json()[
            "code"
        ]
        == "OUT_OF_ORDER"
    )
    assert choose(client, headers, aid, qid, ["unknown"]).status_code == 422
    assert choose(client, headers, aid, qid, ["o1", "o2"]).status_code == 422
    assert choose(client, headers, aid, qid, []).status_code == 422
    first = choose(client, headers, aid, qid, question["correctOptionIds"]).json()
    assert first["isCorrect"]
    assert first["evidence"][0]["sourceUrl"].startswith("https://")
    assert first["explanation"]
    response = choose(client, headers, aid, qid, ["o2"]).json()
    assert response == first
    next_question = client.get(f"/v1/attempts/{aid}", headers=headers).json()["question"]
    assert next_question["index"] == 1


@pytest.mark.parametrize("complete_set", [True, False])
def test_multiple_choice_uses_exact_normalized_set(client, play, fixed_data, complete_set):
    headers, _, attempt = play
    aid = attempt["attemptId"]
    for item in fixed_data["questions"]:
        correct = item["correctOptionIds"]
        if item["type"] == "multi":
            selected = [*reversed(correct), correct[0]] if complete_set else correct[:1]
            result = choose(client, headers, aid, item["questionId"], selected).json()
            assert result["isCorrect"] is complete_set
            assert set(result["correctOptionIds"]) == set(correct)
            break
        assert choose(client, headers, aid, item["questionId"], correct).json()["isCorrect"]


def test_concurrent_choices_share_the_first_receipt(client, play):
    headers, _, attempt = play
    aid, qid = attempt["attemptId"], attempt["question"]["questionId"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda option: choose(client, headers, aid, qid, [option]).json(), ["o1", "o2"]
            )
        )
    assert results[0] == results[1]


def test_completion_is_atomic_and_scores_only_real_answers(client, play, fixed_data, database):
    from app.db import Record, RecordSummary

    headers, _, attempt = play
    aid = attempt["attemptId"]
    finish(client, headers, aid, fixed_data, wrong=True, skip_last=True)
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Record)) == 0
    last = fixed_data["questions"][-1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: choose(
                    client, headers, aid, last["questionId"], last["correctOptionIds"]
                ).json(),
                range(2),
            )
        )
    assert results[0] == results[1]
    rid = results[0]["recordId"]
    detail = client.get(f"/v1/records/{rid}", headers=headers).json()
    assert detail["totalQuestions"] == 20
    assert detail["correctCount"] == 19
    assert len(detail["answers"]) == 20
    assert detail["answers"][-1]["recordId"] == rid
    assert detail["aiSummary"]["status"] == "QUEUED"
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Record)) == 1
        assert db.scalar(select(func.count()).select_from(RecordSummary)) == 1


def test_last_answer_rolls_back_if_record_write_fails(client, play, fixed_data, database, settings):
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import Attempt, AttemptAnswer, Record, RecordSummary
    from app.main import create_app

    headers, _, attempt = play
    aid = attempt["attemptId"]
    finish(client, headers, aid, fixed_data, skip_last=True)
    with database.begin() as db:
        db.execute(
            text("ALTER TABLE records ADD CONSTRAINT test_reject_record CHECK (correct_count < 0)")
        )
    last = fixed_data["questions"][-1]
    with TestClient(create_app(settings, database), raise_server_exceptions=False) as failing:
        result = choose(failing, headers, aid, last["questionId"], last["correctOptionIds"])
    assert result.status_code == 500
    with database.begin() as db:
        assert db.get(Attempt, aid).answered_count == 19
        assert db.get(AttemptAnswer, (aid, last["questionId"])) is None
        assert db.scalar(select(func.count()).select_from(Record)) == 0
        assert db.scalar(select(func.count()).select_from(RecordSummary)) == 0
        db.execute(text("ALTER TABLE records DROP CONSTRAINT test_reject_record"))
    assert choose(client, headers, aid, last["questionId"], last["correctOptionIds"]).json()[
        "recordId"
    ]


def test_history_snapshot_survives_quiz_removal_and_is_private(
    client, play, fixed_data, login, database
):
    from app.db import Quiz

    headers, quiz_id, attempt = play
    rid = finish(client, headers, attempt["attemptId"], fixed_data)[-1]["recordId"]
    with database.begin() as db:
        db.delete(db.get(Quiz, quiz_id))
    response = client.get(f"/v1/records/{rid}", headers=headers)
    assert response.status_code == 200
    assert response.json()["correctCount"] == 20
    assert response.json()["answers"][0]["evidence"][0]["text"]
    other = login("device-888888")
    assert client.get("/v1/records", headers=other).json()["items"] == []
    assert client.get(f"/v1/records/{rid}", headers=other).status_code == 404


def test_history_keyset_handles_identical_completion_times(client, play, fixed_data, database):
    from sqlalchemy import update

    from app.db import Record, utcnow

    headers, quiz_id, attempt = play
    first = finish(client, headers, attempt["attemptId"], fixed_data)[-1]["recordId"]
    another = client.post(
        "/v1/attempts", headers={**headers, "Idempotency-Key": "restart"}, json={"quizId": quiz_id}
    ).json()
    second = finish(client, headers, another["attemptId"], fixed_data)[-1]["recordId"]
    with database.begin() as db:
        db.execute(update(Record).values(completed_at=utcnow()))

    page1 = client.get("/v1/records", params={"limit": 1}, headers=headers).json()
    assert page1["items"][0]["recordId"] == max(first, second)
    page2 = client.get(
        "/v1/records", params={"limit": 1, "cursor": page1["nextCursor"]}, headers=headers
    ).json()
    assert page2["items"][0]["recordId"] == min(first, second)
    assert page2["nextCursor"] is None
    assert (
        client.get("/v1/records", params={"cursor": "?invalid"}, headers=headers).status_code == 422
    )


def test_summary_retry_is_private_and_can_only_be_accepted_once(
    client, play, fixed_data, database, login, settings
):
    from types import SimpleNamespace

    from app.db import RecordSummary
    from app.worker.ai import AIError
    from app.worker.queue import Worker

    headers, _, attempt = play
    record_id = finish(client, headers, attempt["attemptId"], fixed_data, wrong=True)[-1][
        "recordId"
    ]
    path = f"/v1/records/{record_id}/ai-summary/retry"
    with database.begin() as db:
        summary = db.get(RecordSummary, record_id)
        summary.status = "FAILED"
        summary.error_code = "PROVIDER_TIMEOUT"
        summary.error_message = "模型超时。"
        summary.error_retryable = True
        summary.call_count = 2
    failure = client.get(f"/v1/records/{record_id}", headers=headers).json()
    assert failure["aiSummary"]["error"]["retryable"]
    assert failure["correctCount"] == 19
    assert (
        client.post(
            path, headers={**login("device-123999"), "Idempotency-Key": "cross-user"}
        ).status_code
        == 404
    )
    key = {**headers, "Idempotency-Key": "first-retry"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(path, headers=key), range(2)))
    assert [result.status_code for result in results] == [202, 202]
    assert [result.json()["status"] for result in results] == ["QUEUED", "QUEUED"]
    with database() as db:
        summary = db.get(RecordSummary, record_id)
        assert summary.retry_count == 0
        assert summary.call_count == 2

    def reject(_snapshot):
        raise AIError("UNSUITABLE_CONTENT", "模型拒绝处理。", retryable=False)

    assert Worker(settings, database, SimpleNamespace(summarize=reject)).process_once()
    with database() as db:
        summary = db.get(RecordSummary, record_id)
        assert summary.retry_count == 1
        assert summary.call_count == 3
    assert client.post(path, headers=key).json()["status"] == "FAILED"
    assert client.post(path, headers={**headers, "Idempotency-Key": "new-retry"}).status_code == 409


def test_elapsed_summary_can_be_requeued_when_worker_was_offline(
    client, play, fixed_data, database
):
    from app.db import RecordSummary, utcnow

    headers, _, attempt = play
    record_id = finish(client, headers, attempt["attemptId"], fixed_data)[-1]["recordId"]
    with database.begin() as db:
        db.get(RecordSummary, record_id).expires_at = utcnow() - timedelta(seconds=1)
    record = client.get(f"/v1/records/{record_id}", headers=headers).json()
    assert record["aiSummary"]["status"] == "FAILED"
    assert record["aiSummary"]["error"]["retryable"]
    path = f"/v1/records/{record_id}/ai-summary/retry"
    assert (
        client.post(path, headers={**headers, "Idempotency-Key": "after-deadline"}).json()["status"]
        == "QUEUED"
    )


def test_summary_quota_does_not_consume_manual_retry_slot(
    client, play, fixed_data, database, settings
):
    from fastapi.testclient import TestClient

    from app.db import RateLimit, RecordSummary, utcnow
    from app.main import create_app

    headers, _, attempt = play
    record_id = finish(client, headers, attempt["attemptId"], fixed_data)[-1]["recordId"]
    with database.begin() as db:
        summary = db.get(RecordSummary, record_id)
        summary.status = "FAILED"
        summary.error_code = "RATE_LIMITED"
        summary.error_retryable = True
        now = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        db.add(
            RateLimit(
                key="summary:global", window_start=now, expires_at=now + timedelta(days=1), count=1
            )
        )
    constrained = settings.model_copy(update={"global_daily_summary_limit": 1})
    with TestClient(create_app(constrained, database)) as capped:
        response = capped.post(
            f"/v1/records/{record_id}/ai-summary/retry",
            headers={**headers, "Idempotency-Key": "when-full"},
        )
    assert response.status_code == 429
    with database() as db:
        summary = db.get(RecordSummary, record_id)
        assert summary.retry_count == 0
        assert summary.retry_key is None


def test_manual_retry_survives_quota_filled_after_enqueue(
    client, play, fixed_data, database, settings
):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from sqlalchemy import delete

    from app.db import RateLimit, Record, RecordSummary, utcnow
    from app.main import create_app
    from app.quota import reserve_summary_quota
    from app.worker.queue import Worker

    headers, _, attempt = play
    record_id = finish(client, headers, attempt["attemptId"], fixed_data)[-1]["recordId"]
    constrained = settings.model_copy(update={"global_daily_summary_limit": 1})
    with database.begin() as db:
        user_id = db.get(Record, record_id).user_id
        task = db.get(RecordSummary, record_id)
        task.status = "FAILED"
        task.error_code = "PROVIDER_TIMEOUT"
        task.error_retryable = True
        task.call_count = constrained.max_summary_calls - 1

    called = []
    item = {"text": "复习题目中的概念。", "questionIds": ["seasons-01"], "evidenceIds": []}

    def summarize(_snapshot):
        called.append(True)
        return {"knowledgeReview": [item], "mistakeNotes": [], "reviewSuggestions": [item]}

    worker = Worker(constrained, database, SimpleNamespace(summarize=summarize))
    path = f"/v1/records/{record_id}/ai-summary/retry"
    with TestClient(create_app(constrained, database)) as capped:
        assert capped.post(path, headers={**headers, "Idempotency-Key": "first"}).status_code == 202
        with database.begin() as db:
            reserve_summary_quota(db, user_id, utcnow(), constrained)
        assert worker.process_once()
        with database.begin() as db:
            task = db.get(RecordSummary, record_id)
            assert task.error_code == "RATE_LIMITED"
            assert task.retry_count == 0
            assert task.error_retryable
            assert task.call_count == constrained.max_summary_calls - 1
            assert not called
            db.execute(delete(RateLimit))
        assert (
            capped.post(path, headers={**headers, "Idempotency-Key": "first"}).json()["status"]
            == "FAILED"
        )
        assert capped.post(path, headers={**headers, "Idempotency-Key": "next"}).status_code == 202
        with database.begin() as db:
            reserve_summary_quota(db, user_id, utcnow(), constrained)
        assert worker.process_once()
        with database.begin() as db:
            db.execute(delete(RateLimit))
        # Replaying an older accepted key cannot re-enqueue an unpaid retry.
        assert (
            capped.post(path, headers={**headers, "Idempotency-Key": "first"}).json()["status"]
            == "FAILED"
        )
        assert capped.post(path, headers={**headers, "Idempotency-Key": "third"}).status_code == 202
        with database.begin() as db:
            task = db.get(RecordSummary, record_id)
            task.status = "RUNNING"
            task.lease_until = utcnow() - timedelta(seconds=1)
        assert worker.process_once()
    with database() as db:
        task = db.get(RecordSummary, record_id)
        assert (task.status, task.retry_count, task.call_count) == (
            "SUCCEEDED",
            1,
            constrained.max_summary_calls,
        )
    assert called == [True]


def test_takeaway_only_evidence_survives_quiz_cleanup_and_reaches_summary(
    client, play, fixed_data, database, settings
):
    from types import SimpleNamespace

    from app.db import Quiz
    from app.worker.queue import Worker

    headers, quiz_id, attempt = play
    with database.begin() as db:
        quiz = db.get(Quiz, quiz_id)
        source = {**quiz.evidence[0], "evidenceId": "takeaway-only"}
        quiz.evidence = [*quiz.evidence, source]
        quiz.content = {
            **quiz.content,
            "takeaways": [{"text": source["text"], "evidenceIds": [source["evidenceId"]]}],
        }

    record_id = finish(client, headers, attempt["attemptId"], fixed_data)[-1]["recordId"]
    with database.begin() as db:
        db.delete(db.get(Quiz, quiz_id))

    def summarize(snapshot):
        assert [evidence["evidenceId"] for evidence in snapshot["takeawayEvidence"]] == [
            "takeaway-only"
        ]
        note = {"text": "回顾地轴倾斜。", "questionIds": [], "evidenceIds": ["takeaway-only"]}
        return {"knowledgeReview": [note], "mistakeNotes": [], "reviewSuggestions": [note]}

    assert Worker(settings, database, SimpleNamespace(summarize=summarize)).process_once()
    result = client.get(f"/v1/records/{record_id}", headers=headers).json()
    assert result["aiSummary"]["status"] == "SUCCEEDED"
    assert result["takeawayEvidence"][0]["sourceUrl"] == source["sourceUrl"]
    assert result["takeawayEvidence"][0]["evidenceId"] == "takeaway-only"


@pytest.mark.parametrize("keep_quiz", [True, False])
def test_migration_backfills_takeaway_sources_from_retained_records(
    client, play, fixed_data, database, keep_quiz
):
    from alembic.config import Config

    from alembic import command
    from app.db import Quiz

    headers, quiz_id, attempt = play
    question_source_id = fixed_data["questions"][0]["evidenceIds"][0]
    with database.begin() as db:
        quiz = db.get(Quiz, quiz_id)
        source = next(item for item in quiz.evidence if item["evidenceId"] == question_source_id)
        snapshot_source = {**source, "evidenceId": "retained-quiz-only"}
        quiz.evidence = [*quiz.evidence, snapshot_source]
        quiz.content = {
            **quiz.content,
            "takeaways": [
                {"text": source["text"], "evidenceIds": [question_source_id, "retained-quiz-only"]}
            ],
        }
    record_id = finish(client, headers, attempt["attemptId"], fixed_data)[-1]["recordId"]
    if not keep_quiz:
        with database.begin() as db:
            db.delete(db.get(Quiz, quiz_id))

    config = Config(str(DATA.parents[1] / "alembic.ini"))
    with database.kw["bind"].begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0005_summary_retry_keys")
        command.upgrade(config, "head")

    result = client.get(f"/v1/records/{record_id}", headers=headers)
    expected = {question_source_id, "retained-quiz-only"} if keep_quiz else {question_source_id}
    assert {item["evidenceId"] for item in result.json()["takeawayEvidence"]} == expected
