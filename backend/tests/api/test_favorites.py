from app.db import Quiz
from tests.topic_fixtures import create_test_quiz


def test_favorites_are_user_scoped_and_toggle_idempotently(client, login, database):
    owner = login('device-first')
    other = login('device-second')
    quiz = create_test_quiz(client, owner)
    attempt = client.post('/v1/attempts', json=quiz, headers={**owner, 'Idempotency-Key': 'favorite-start'}).json()
    question_id = attempt['question']['questionId']
    url = f"/v1/attempts/{attempt['attemptId']}/favorites/{question_id}"
    first = client.put(url, headers=owner)
    repeated = client.put(url, headers=owner)
    assert first.status_code == repeated.status_code == 200
    assert first.json()['fingerprint'] == repeated.json()['fingerprint']
    assert len(client.get('/v1/favorites', headers=owner).json()['items']) == 1
    assert client.get('/v1/favorites', headers=other).json()['items'] == []
    fingerprint = first.json()['fingerprint']
    practice = client.post(f'/v1/favorites/{fingerprint}/practice', headers={**owner, 'Idempotency-Key': 'favorite-practice'})
    assert practice.status_code == 201, practice.text
    assert practice.json()['totalQuestions'] == 1
    with database() as db:
        question = db.get(Quiz, quiz['quizId']).content['questions'][0]
    wrong = next(option['optionId'] for option in question['options'] if option['optionId'] not in question['correctOptionIds'])
    response = client.put(f"/v1/attempts/{practice.json()['attemptId']}/answers/{question_id}", json={'selectedOptionIds': [wrong]}, headers=owner)
    assert response.status_code == 200
    assert response.json()['isCorrect'] is False
    assert response.json()['coinSettlementNew'] is False
    assert len(client.get('/v1/mistakes', headers=owner).json()['items']) == 1
    assert client.delete(f'/v1/favorites/{fingerprint}', headers=other).status_code == 404
    assert client.delete(f'/v1/favorites/{fingerprint}', headers=owner).status_code == 204
    assert client.get('/v1/favorites', headers=owner).json()['items'] == []
