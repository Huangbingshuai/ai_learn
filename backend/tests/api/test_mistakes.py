from datetime import timedelta

from sqlalchemy import select

from app.db import MistakeItem, utcnow


def seed_item(database, user_id, suffix, *, status="TO_REVIEW", minutes_ago=0, topic="seasons"):
    now = utcnow() - timedelta(minutes=minutes_ago)
    fingerprint = suffix * 64
    with database.begin() as db:
        db.add(
            MistakeItem(
                user_id=user_id,
                fingerprint=fingerprint,
                question_snapshot={"stem": f"Question {suffix}"},
                latest_receipt={"isCorrect": False},
                topic_key=topic,
                topic_title=topic.title(),
                wrong_count=2,
                first_wrong_at=now - timedelta(days=1),
                last_wrong_at=now,
                status=status,
                mastered_at=now if status == "MASTERED" else None,
                created_at=now,
                updated_at=now,
            )
        )
    return fingerprint


def test_mistake_list_is_user_scoped_and_reports_pending_count_and_topics(client, login, database):
    first_headers = login("device-first")
    second_headers = login("device-second")
    first_user = client.get("/v1/me", headers=first_headers).json()["userId"]
    second_user = client.get("/v1/me", headers=second_headers).json()["userId"]
    seed_item(database, first_user, "a", topic="seasons")
    seed_item(database, first_user, "b", status="MASTERED", topic="moon")
    seed_item(database, second_user, "c", topic="moon")

    response = client.get("/v1/mistakes", headers=first_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["fingerprint"] for item in body["items"]] == ["a" * 64]
    assert body["pendingCount"] == 1
    assert {topic["topicKey"] for topic in body["topics"]} == {"seasons", "moon"}


def test_mistake_list_filters_status_and_topic_and_uses_stable_cursor(client, login, database):
    headers = login()
    user_id = client.get("/v1/me", headers=headers).json()["userId"]
    seed_item(database, user_id, "a", minutes_ago=1, topic="seasons")
    seed_item(database, user_id, "b", minutes_ago=1, topic="moon")
    seed_item(database, user_id, "c", status="MASTERED", minutes_ago=3, topic="moon")

    first = client.get("/v1/mistakes?status=to_review&limit=1", headers=headers).json()
    second = client.get(
        f"/v1/mistakes?status=to_review&limit=1&cursor={first['nextCursor']}",
        headers=headers,
    ).json()
    filtered = client.get("/v1/mistakes?status=mastered&topicKey=moon", headers=headers).json()

    assert [item["fingerprint"] for item in first["items"]] == ["b" * 64]
    assert [item["fingerprint"] for item in second["items"]] == ["a" * 64]
    assert second["nextCursor"] is None
    assert [item["fingerprint"] for item in filtered["items"]] == ["c" * 64]
    assert first["pendingCount"] == second["pendingCount"] == 2


def test_mistake_detail_and_status_updates_only_affect_owner(client, login, database):
    headers = login("device-first")
    other_headers = login("device-second")
    user_id = client.get("/v1/me", headers=headers).json()["userId"]
    fingerprint = seed_item(database, user_id, "d")

    detail = client.get(f"/v1/mistakes/{fingerprint}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["question"]["stem"] == "Question d"
    assert detail.json()["latestReceipt"]["isCorrect"] is False

    hidden = client.get(f"/v1/mistakes/{fingerprint}", headers=other_headers)
    hidden_patch = client.patch(
        f"/v1/mistakes/{fingerprint}",
        json={"status": "mastered"},
        headers=other_headers,
    )
    changed = client.patch(
        f"/v1/mistakes/{fingerprint}", json={"status": "mastered"}, headers=headers
    )
    assert hidden.status_code == 404
    assert hidden_patch.status_code == 404
    assert changed.status_code == 200, changed.text
    assert changed.json()["status"] == "mastered"

    reopened = client.patch(
        f"/v1/mistakes/{fingerprint}", json={"status": "to_review"}, headers=headers
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "to_review"
    with database() as db:
        stored = db.scalar(select(MistakeItem).where(MistakeItem.fingerprint == fingerprint))
    assert stored.mastered_at is None


def test_mistake_api_rejects_bad_cursor_or_status_and_hides_unknown_items(client, login):
    headers = login()

    bad_cursor = client.get("/v1/mistakes?cursor=not-a-cursor", headers=headers)
    bad_status = client.get("/v1/mistakes?status=archived", headers=headers)
    missing = client.get(f"/v1/mistakes/{'f' * 64}", headers=headers)
    fingerprint = seed_item(
        client.app.state.session_factory,
        client.get("/v1/me", headers=headers).json()["userId"],
        "e",
    )
    forged_owner = client.patch(
        f"/v1/mistakes/{fingerprint}",
        json={"status": "mastered", "userId": "another-user"},
        headers=headers,
    )

    assert bad_cursor.status_code == 422
    assert bad_status.status_code == 422
    assert missing.status_code == 404
    assert forged_owner.status_code == 422
