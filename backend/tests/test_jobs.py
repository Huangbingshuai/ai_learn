from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import GenerationJob, RateLimit, utcnow
from app.main import create_app


def submit(client, headers, topic, key):
    return client.post(
        "/v1/generation-jobs", json={"topic": topic}, headers={**headers, "Idempotency-Key": key}
    )


def test_generation_idempotency_binds_input_and_quota(client, database, login):
    headers = login()
    first = submit(client, headers, "　为什么会有 四季？", "topic")
    assert first.status_code == 202
    job = first.json()
    assert job["status"] == "QUEUED"
    assert job["topicTitle"] == "四季成因"
    assert job["quizId"] is None
    same = submit(client, headers, "为什么会有四季", "topic")
    assert same.json() == job
    assert submit(client, headers, "月相变化", "topic").status_code == 409
    with database() as db:
        rows = db.scalars(select(GenerationJob)).all()
        assert len(rows) == 1
        assert rows[0].topic_version == "v1"
        assert len(rows[0].input_digest) == 64
        assert [
            row.count
            for row in db.scalars(select(RateLimit).where(RateLimit.key.like("generation:%")))
        ] == [1, 1]
    assert client.get(f"/v1/generation-jobs/{job['jobId']}", headers=headers).json() == job
    assert (
        client.get(
            f"/v1/generation-jobs/{job['jobId']}", headers=login("other-123456789")
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "topic, code",
    [
        ("", "INVALID_INPUT"),
        ("季", "INVALID_INPUT"),
        ("四" * 81, "INVALID_INPUT"),
        ("？ ！", "INVALID_INPUT"),
    ],
)
def test_invalid_topic_does_not_enqueue(client, login, database, topic, code):
    result = submit(client, login(), topic, "invalid")
    assert result.status_code == 422
    assert result.json()["code"] == code
    with database() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 0


def test_new_topic_enqueues_without_catalog_match(client, login, database):
    headers = login()
    job = submit(client, headers, "什么是 Vibe Coding？", "live")
    assert job.status_code == 202
    result = job.json()
    assert result["topicTitle"] == "什么是 Vibe Coding？"
    assert result["status"] == "QUEUED"
    assert result["createdAt"]
    assert submit(client, headers, "什么是 vibe-coding", "live").json() == result
    with database() as db:
        row = db.get(GenerationJob, result["jobId"])
        assert row.topic_key is None and row.topic_version is None


def test_atomic_job_quota_and_concurrent_idempotency(database, settings):
    config = settings.model_copy(update={"user_daily_generation_limit": 1})
    with TestClient(create_app(config, database)) as client:
        token = client.post("/v1/dev/auth", json={"deviceId": "device-unique-123"}).json()[
            "accessToken"
        ]
        headers = {"Authorization": f"Bearer {token}"}
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(lambda _: submit(client, headers, "四季", "duplicate"), range(2))
            )
        assert [response.status_code for response in responses] == [202, 202]
        assert responses[0].json() == responses[1].json()
        assert submit(client, headers, "月相变化", "different").status_code == 429
    with database() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob)) == 1
        assert [
            row.count
            for row in db.scalars(select(RateLimit).where(RateLimit.key.like("generation:%")))
        ] == [1, 1]


def test_expired_generation_without_worker_has_terminal_projection(client, login, database):
    headers = login()
    job_id = submit(client, headers, "月相变化", "one").json()["jobId"]
    with database.begin() as db:
        db.get(GenerationJob, job_id).expires_at = utcnow() - timedelta(seconds=1)
    result = client.get(
        f"/v1/generation-jobs/{job_id}", headers={**headers, "X-Request-ID": "timeout"}
    )
    assert result.status_code == 200
    assert result.json()["status"] == "FAILED"
    assert result.json()["error"] == {
        "code": "PROVIDER_TIMEOUT",
        "message": "生成超时了，可以重新生成或换个主题。",
        "requestId": "timeout",
        "retryable": True,
    }


def test_large_request_is_rejected_without_logging_input(client, login):
    data = "x" * 34000
    result = client.post("/v1/generation-jobs", json={"topic": data}, headers=login())
    assert result.status_code == 413
    assert result.json()["code"] == "REQUEST_TOO_LARGE"
    assert data[0:20] not in result.text


def test_job_poll_serves_only_successfully_published_quizzes(client, database, login, settings):
    from app.domain.topics import load_fixed_quiz
    from app.worker.queue import Worker

    class Model:
        def generate(self, _topic):
            return load_fixed_quiz()

        def review(self, _topic, _quiz):
            return {"accepted": True, "issues": []}

    headers = login()
    job = submit(client, headers, "四季", "generate").json()
    path = f"/v1/generation-jobs/{job['jobId']}"
    assert Worker(settings, database, Model()).process_once()
    result = client.get(path, headers=headers).json()
    assert result["status"] == "SUCCEEDED"
    assert result["quizExpiresAt"]
    start = client.post(
        "/v1/attempts",
        headers={**headers, "Idempotency-Key": "generated-attempt"},
        json={"quizId": result["quizId"]},
    ).json()
    assert start["question"]["aiGenerated"]
    assert "correctOptionIds" not in start["question"]
    from app.db import Quiz

    with database.begin() as db:
        db.get(Quiz, result["quizId"]).expires_at = utcnow() - timedelta(seconds=1)
    unavailable = client.get(path, headers=headers).json()
    assert unavailable["quizId"] is None
    assert unavailable["error"]["code"] == "QUIZ_EXPIRED"


def test_job_poll_returns_only_public_provider_failure(client, database, login):
    headers = login()
    job = submit(client, headers, "四季", "one").json()
    with database.begin() as db:
        row = db.get(GenerationJob, job["jobId"])
        row.status = "FAILED"
        row.phase = None
        row.error_code = "UNSUITABLE_CONTENT"
        row.error_message = "内容未通过检查。"
    result = client.get(f"/v1/generation-jobs/{job['jobId']}", headers=headers).json()
    assert result["status"] == "FAILED"
    assert result["quizId"] is None
    assert set(result["error"]) == {"code", "message", "requestId", "retryable"}
    assert not result["error"]["retryable"]
