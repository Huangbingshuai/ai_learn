from sqlalchemy import CheckConstraint

from app.db import MistakeItem


def test_mistake_item_has_user_scoped_identity_and_review_snapshot():
    table = MistakeItem.__table__
    constraints = {item.name: item for item in table.constraints}
    indexes = {item.name for item in table.indexes}

    assert "user_id" in table.c
    assert "fingerprint" in table.c
    assert "question_snapshot" in table.c
    assert "latest_receipt" in table.c
    assert "wrong_count" in table.c
    assert "first_wrong_at" in table.c
    assert "last_wrong_at" in table.c
    assert "status" in table.c
    assert "mastered_at" in table.c
    assert "uq_mistake_user_fingerprint" in constraints
    assert {column.name for column in constraints["uq_mistake_user_fingerprint"].columns} == {
        "user_id",
        "fingerprint",
    }
    assert any(
        isinstance(item, CheckConstraint) and "wrong_count" in str(item.sqltext)
        for item in constraints.values()
    )
    assert "ix_mistakes_user_recent" in indexes


def test_mistake_item_defaults_to_reviewable():
    assert MistakeItem.__table__.c.status.default.arg == "TO_REVIEW"
    assert MistakeItem.__table__.c.status.server_default is not None
