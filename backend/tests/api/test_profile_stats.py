from app.db import Record, new_id, utcnow
from tests.topic_fixtures import create_test_quiz


def test_profile_stats_aggregate_only_the_authenticated_users_completed_learning(
    client, login, database
):
    owner = login("stats-owner-device")
    other = login("stats-other-device")
    owner_id = client.get("/v1/me", headers=owner).json()["userId"]
    other_id = client.get("/v1/me", headers=other).json()["userId"]

    with database() as db, db.begin():
        db.add_all(
            [
                Record(
                    attempt_id=new_id(),
                    user_id=owner_id,
                    topic_title="天文",
                    completed_at=utcnow(),
                    total_questions=12,
                    correct_count=9,
                    takeaways=[],
                    answers=[],
                ),
                Record(
                    attempt_id=new_id(),
                    user_id=owner_id,
                    topic_title="历史",
                    completed_at=utcnow(),
                    total_questions=8,
                    correct_count=7,
                    takeaways=[],
                    answers=[],
                ),
                Record(
                    attempt_id=new_id(),
                    user_id=other_id,
                    topic_title="私有主题",
                    completed_at=utcnow(),
                    total_questions=100,
                    correct_count=100,
                    takeaways=[],
                    answers=[],
                ),
            ]
        )

    stats = client.get("/v1/me/stats", headers=owner)
    assert stats.status_code == 200
    assert stats.json() == {
        "completedSets": 2,
        "answeredQuestions": 20,
        "correctAnswers": 16,
        "accuracyPercent": 80,
    }


def test_profile_stats_return_zeroes_before_any_completed_learning(client, login):
    stats = client.get("/v1/me/stats", headers=login())
    assert stats.status_code == 200
    assert stats.json() == {
        "completedSets": 0,
        "answeredQuestions": 0,
        "correctAnswers": 0,
        "accuracyPercent": 0,
    }


def test_completed_sets_increases_only_after_last_question(client, login, fixed_data):
    headers = login()
    quiz = create_test_quiz(client, headers)
    started = client.post('/v1/attempts', json=quiz, headers={**headers, 'Idempotency-Key': 'stats-full-set'})
    assert started.status_code == 201
    attempt_id = started.json()['attemptId']
    questions = fixed_data['questions']
    for index, question in enumerate(questions):
        before = client.get('/v1/me/stats', headers=headers).json()
        assert before['completedSets'] == 0
        response = client.put(
            f"/v1/attempts/{attempt_id}/answers/{question['questionId']}",
            json={'selectedOptionIds': question['correctOptionIds']}, headers=headers,
        )
        assert response.status_code == 200, (index, response.text)
    stats = client.get('/v1/me/stats', headers=headers).json()
    assert stats['completedSets'] == 1
    assert stats['answeredQuestions'] == len(questions)
