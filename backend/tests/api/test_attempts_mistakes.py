from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.db import MistakeAnswerEvent, MistakeItem


def start_quiz(client, headers):
    response = client.post("/v1/dev/quizzes/fixed", headers={**headers, "Idempotency-Key": "fixed"})
    assert response.status_code == 201, response.text
    return response.json()["quizId"]


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


def test_wrong_answer_is_added_to_the_book_before_attempt_finishes(
    client, login, fixed_data, database
):
    headers = login()
    quiz_id = start_quiz(client, headers)
    attempt_id = start_attempt(client, headers, quiz_id, "start")
    response = answer_wrong(client, headers, attempt_id, fixed_data["questions"][0])

    assert response.status_code == 200, response.text
    assert response.json()["isCorrect"] is False
    with database() as db:
        user_id = client.get("/v1/me", headers=headers).json()["userId"]
        items = db.scalars(select(MistakeItem).where(MistakeItem.user_id == user_id)).all()
        events = db.scalars(select(MistakeAnswerEvent)).all()
    assert len(items) == 1
    assert items[0].wrong_count == 1
    assert items[0].latest_receipt["isCorrect"] is False
    assert len(events) == 1


def test_replaying_the_same_wrong_answer_does_not_count_twice(client, login, fixed_data, database):
    headers = login()
    quiz_id = start_quiz(client, headers)
    attempt_id = start_attempt(client, headers, quiz_id, "start")
    first = answer_wrong(client, headers, attempt_id, fixed_data["questions"][0])
    replay = answer_wrong(client, headers, attempt_id, fixed_data["questions"][0])

    assert replay.json() == first.json()
    with database() as db:
        item = db.scalar(select(MistakeItem))
        event_count = db.scalar(select(func.count()).select_from(MistakeAnswerEvent))
    assert item.wrong_count == 1
    assert event_count == 1


def test_wrong_answers_from_distinct_attempts_are_not_lost_under_concurrency(
    client, login, fixed_data, database
):
    headers = login()
    quiz_id = start_quiz(client, headers)
    attempts = [start_attempt(client, headers, quiz_id, f"start-{index}") for index in range(2)]
    question = fixed_data["questions"][0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda attempt_id: answer_wrong(client, headers, attempt_id, question), attempts
            )
        )

    assert [response.status_code for response in responses] == [200, 200]
    with database() as db:
        items = db.scalars(select(MistakeItem)).all()
        events = db.scalars(select(MistakeAnswerEvent)).all()
    assert len(items) == 1
    assert items[0].wrong_count == 2
    assert len(events) == 2
