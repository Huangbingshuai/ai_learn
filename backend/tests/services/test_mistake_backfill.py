from sqlalchemy import delete, func, select, update

from app.db import Attempt, MistakeAnswerEvent, MistakeItem, Record, new_id, utcnow
from app.services.mistakes import backfill_mistakes
from tests.topic_fixtures import create_test_quiz


def start_quiz(client, headers):
    return create_test_quiz(client, headers)["quizId"]


def start_attempt(client, headers, quiz_id, key):
    response = client.post(
        "/v1/attempts",
        json={"quizId": quiz_id},
        headers={**headers, "Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["attemptId"]


def answer_wrong(client, headers, attempt_id, question):
    wrong_option = next(
        option["optionId"]
        for option in question["options"]
        if option["optionId"] not in question["correctOptionIds"]
    )
    return client.put(
        f"/v1/attempts/{attempt_id}/answers/{question['questionId']}",
        json={"selectedOptionIds": [wrong_option]},
        headers=headers,
    )


def test_backfill_merges_completed_records_and_incomplete_attempts_idempotently(
    client, login, fixed_data, database
):
    headers = login()
    quiz_id = start_quiz(client, headers)
    completed_attempt = start_attempt(client, headers, quiz_id, "completed")
    for index, question in enumerate(fixed_data["questions"]):
        if index == 0:
            response = answer_wrong(client, headers, completed_attempt, question)
        else:
            response = client.put(
                f"/v1/attempts/{completed_attempt}/answers/{question['questionId']}",
                json={"selectedOptionIds": question["correctOptionIds"]},
                headers=headers,
            )
        assert response.status_code == 200, response.text

    incomplete_attempt = start_attempt(client, headers, quiz_id, "incomplete")
    response = answer_wrong(client, headers, incomplete_attempt, fixed_data["questions"][0])
    assert response.status_code == 200, response.text

    with database.begin() as db:
        db.execute(delete(MistakeItem))
        db.execute(delete(MistakeAnswerEvent))
        db.execute(delete(Attempt).where(Attempt.id == completed_attempt))
        backfill_mistakes(db, batch_size=1)

    with database.begin() as db:
        db.execute(update(MistakeItem).values(status="MASTERED"))
        backfill_mistakes(db, batch_size=1)

    with database() as db:
        item = db.scalar(select(MistakeItem))
        event_count = db.scalar(select(func.count()).select_from(MistakeAnswerEvent))
    assert item.wrong_count == 2
    assert event_count == 2
    assert item.status == "MASTERED"
    assert item.topic_key == "test-topic"
    assert item.latest_receipt["isCorrect"] is False


def test_backfill_skips_a_malformed_historical_receipt(client, login, database):
    headers = login()
    user_id = client.get("/v1/me", headers=headers).json()["userId"]
    with database.begin() as db:
        db.add(
            Record(
                id=new_id(),
                attempt_id=new_id(),
                user_id=user_id,
                topic_title="四季的成因",
                completed_at=utcnow(),
                total_questions=1,
                correct_count=0,
                takeaways=[],
                takeaway_evidence=[],
                answers=["malformed receipt"],
            )
        )

    with database.begin() as db:
        imported = backfill_mistakes(db, batch_size=1)

    assert imported == 0
