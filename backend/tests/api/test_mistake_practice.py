from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import func, select

from app.db import (
    Attempt,
    CoinSettlement,
    GenerationJob,
    MistakeItem,
    Quiz,
    RateLimit,
    Record,
    RecordSummary,
)
from tests.topic_fixtures import create_test_quiz


def create_mistake(client, headers, fixed_data):
    quiz = create_test_quiz(client, headers)
    started = client.post(
        "/v1/attempts",
        json={"quizId": quiz["quizId"]},
        headers={**headers, "Idempotency-Key": "learn"},
    )
    assert started.status_code == 201, started.text
    attempt = started.json()
    question = fixed_data["questions"][0]
    wrong = next(
        option["optionId"]
        for option in question["options"]
        if option["optionId"] not in question["correctOptionIds"]
    )
    answered = client.put(
        f"/v1/attempts/{attempt['attemptId']}/answers/{question['questionId']}",
        json={"selectedOptionIds": [wrong]},
        headers=headers,
    )
    assert answered.status_code == 200, answered.text
    return attempt, question


def start_practice(client, headers, fingerprint, key="practice"):
    return client.post(
        f"/v1/mistakes/{fingerprint}/practice",
        headers={**headers, "Idempotency-Key": key},
    )


def test_practice_uses_one_server_judged_question_without_record_or_summary(
    client, login, fixed_data, database
):
    headers = login()
    _, question = create_mistake(client, headers, fixed_data)
    with database() as db:
        mistake = db.scalar(select(MistakeItem))
        fingerprint = mistake.fingerprint

    mastered = client.patch(
        f"/v1/mistakes/{fingerprint}",
        json={"status": "mastered"},
        headers=headers,
    )
    assert mastered.status_code == 200
    practice = start_practice(client, headers, fingerprint)
    assert practice.status_code == 201, practice.text
    attempt = practice.json()
    assert attempt["totalQuestions"] == 1
    assert attempt["purpose"] == "PRACTICE"
    assert attempt["question"]["stem"] == question["stem"]

    replay = start_practice(client, headers, fingerprint)
    assert replay.json()["attemptId"] == attempt["attemptId"]
    correct = client.put(
        f"/v1/attempts/{attempt['attemptId']}/answers/{attempt['question']['questionId']}",
        json={"selectedOptionIds": question["correctOptionIds"]},
        headers=headers,
    )

    assert correct.status_code == 200, correct.text
    assert correct.json()["isCorrect"] is True
    assert correct.json()["coinDelta"] == 0
    with database() as db:
        stored_attempt = db.get(Attempt, attempt["attemptId"])
        record_count = db.scalar(select(func.count()).select_from(Record))
        summary_count = db.scalar(select(func.count()).select_from(RecordSummary))
        quiz = db.get(Quiz, stored_attempt.quiz_id)
        job = db.get(GenerationJob, quiz.job_id)
        settlement_count = db.scalar(select(func.count()).select_from(CoinSettlement))
        stored_mistake = db.scalar(
            select(MistakeItem).where(MistakeItem.fingerprint == fingerprint)
        )
    assert stored_attempt.purpose == "PRACTICE"
    assert job.source == "PRACTICE"
    assert stored_mistake.status == "MASTERED"
    assert record_count == 0
    assert summary_count == 0
    assert settlement_count == 1


def test_wrong_practice_reopens_mastered_item_and_increments_once(
    client, login, fixed_data, database
):
    headers = login()
    _, question = create_mistake(client, headers, fixed_data)
    with database() as db:
        item = db.scalar(select(MistakeItem))
        fingerprint = item.fingerprint
    client.patch(f"/v1/mistakes/{fingerprint}", json={"status": "mastered"}, headers=headers)

    attempt = start_practice(client, headers, fingerprint).json()
    wrong_option = next(
        option["optionId"]
        for option in attempt["question"]["options"]
        if option["optionId"] not in question["correctOptionIds"]
    )
    answer_url = f"/v1/attempts/{attempt['attemptId']}/answers/{attempt['question']['questionId']}"
    body = {"selectedOptionIds": [wrong_option]}
    first = client.put(answer_url, json=body, headers=headers)
    replay = client.put(answer_url, json=body, headers=headers)

    assert first.status_code == 200, first.text
    assert replay.json() == first.json()
    with database() as db:
        stored = db.scalar(select(MistakeItem).where(MistakeItem.fingerprint == fingerprint))
    assert stored.status == "TO_REVIEW"
    assert stored.wrong_count == 2
    assert stored.latest_receipt["isCorrect"] is False


def test_practice_requires_owned_mistake_and_conflicts_on_reused_key(client, login, fixed_data):
    first_headers = login("device-first")
    other_headers = login("device-other")
    _, _ = create_mistake(client, first_headers, fixed_data)
    with client.app.state.session_factory() as db:
        fingerprint = db.scalar(select(MistakeItem.fingerprint))

    hidden = start_practice(client, other_headers, fingerprint)
    first = start_practice(client, first_headers, fingerprint, key="same-key")
    other = create_test_quiz(client, first_headers)
    assert hidden.status_code == 404
    assert first.status_code == 201
    assert other["quizId"]
    normal = client.post(
        "/v1/attempts",
        json={"quizId": other["quizId"]},
        headers={**first_headers, "Idempotency-Key": "same-key"},
    )
    assert normal.status_code == 409


def test_concurrent_practice_retries_create_one_attempt_and_reserve_one_quota(
    client, login, fixed_data, database
):
    headers = login()
    create_mistake(client, headers, fixed_data)
    with database() as db:
        item = db.scalar(select(MistakeItem))
        fingerprint = item.fingerprint
    barrier = Barrier(2)

    def start():
        barrier.wait()
        return start_practice(client, headers, fingerprint, key="concurrent-practice")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: start(), range(2)))

    assert first.status_code == second.status_code == 201
    assert first.json()["attemptId"] == second.json()["attemptId"]
    user_id = client.get("/v1/me", headers=headers).json()["userId"]
    with database() as db:
        reservations = db.scalar(
            select(RateLimit.count).where(RateLimit.key == f"attempt:user:{user_id}")
        )
        practice_count = db.scalar(
            select(func.count()).select_from(Attempt).where(Attempt.purpose == "PRACTICE")
        )
    assert reservations == 2
    assert practice_count == 1
