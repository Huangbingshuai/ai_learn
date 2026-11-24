import json
from datetime import timedelta
from pathlib import Path

import pytest

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
    assert set(attempt["question"]) == {"questionId", "type", "stem", "options", "index", "aiGenerated"}
    assert attempt["question"]["index"] == 0
    assert attempt["question"]["aiGenerated"] is False
    start = client.post("/v1/attempts", json={"quizId": quiz_id}, headers={**headers, "Idempotency-Key": "start"})
    assert start.json()["attemptId"] == attempt["attemptId"]
    same = client.post("/v1/dev/quizzes/fixed", headers={**headers, "Idempotency-Key": "fixed"})
    assert same.json()["quizId"] == quiz_id
    other = login("device-999999")
    assert client.get(f"/v1/attempts/{attempt['attemptId']}", headers=other).status_code == 404
    assert client.post("/v1/attempts", json={"quizId": quiz_id}, headers={**other, "Idempotency-Key": "x"}).status_code == 404


def test_expired_quiz_cannot_be_reopened(client, play, database):
    from app.db import Quiz, utcnow

    headers, quiz_id, attempt = play
    with database.begin() as db:
        db.get(Quiz, quiz_id).expires_at = utcnow() - timedelta(seconds=1)
    response = client.post("/v1/attempts", json={"quizId": quiz_id}, headers={**headers, "Idempotency-Key": "restart"})
    assert (response.status_code, response.json()["code"]) == (409, "QUIZ_EXPIRED")
    current = client.get(f"/v1/attempts/{attempt['attemptId']}", headers=headers).json()
    assert current["status"] == "ABANDONED"
    assert current["question"] is None


