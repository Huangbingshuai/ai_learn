"""User-owned favorite questions."""
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert

from app.api.dependencies import DB, CurrentUser
from app.api.errors import not_found
from app.db import Attempt, FavoriteItem, Quiz, SavedTopic, utcnow
from app.domain.questions import question_fingerprint

router = APIRouter()


class FavoriteView(BaseModel):
    fingerprint: str
    topicKey: str
    topicTitle: str


def _owned_question(db, user_id, attempt_id, question_id):
    row = db.execute(select(Attempt, Quiz).join(Quiz, Quiz.id == Attempt.quiz_id).where(
        Attempt.id == str(attempt_id), Attempt.user_id == user_id,
        exists().where(SavedTopic.user_id == user_id, SavedTopic.topic_key == Quiz.topic_key),
    )).first()
    if not row:
        raise not_found()
    _, quiz = row
    question = next((q for q in quiz.content['questions'] if q['questionId'] == question_id), None)
    if question is None:
        raise not_found()
    return quiz, question


@router.put('/attempts/{attempt_id}/favorites/{question_id}', response_model=FavoriteView)
def add_favorite(attempt_id: UUID, question_id: str, db: DB, user_id: CurrentUser):
    with db.begin():
        quiz, question = _owned_question(db, user_id, attempt_id, question_id)
        fingerprint = question_fingerprint(question)
        db.execute(insert(FavoriteItem).values(
            user_id=user_id, fingerprint=fingerprint, question_snapshot=question,
            evidence=quiz.evidence, topic_key=quiz.topic_key, topic_title=quiz.topic_title,
            created_at=utcnow(),
        ).on_conflict_do_nothing(constraint='uq_favorite_user_fingerprint'))
        item = db.scalar(select(FavoriteItem).where(FavoriteItem.user_id == user_id, FavoriteItem.fingerprint == fingerprint))
        return FavoriteView(fingerprint=item.fingerprint, topicKey=item.topic_key, topicTitle=item.topic_title)


@router.get('/attempts/{attempt_id}/favorites/{question_id}')
def favorite_status(attempt_id: UUID, question_id: str, db: DB, user_id: CurrentUser):
    _, question = _owned_question(db, user_id, attempt_id, question_id)
    fingerprint = question_fingerprint(question)
    found = db.scalar(select(FavoriteItem.id).where(FavoriteItem.user_id == user_id, FavoriteItem.fingerprint == fingerprint))
    return {'fingerprint': fingerprint, 'favorited': found is not None}


@router.get('/favorites')
def list_favorites(db: DB, user_id: CurrentUser):
    rows = db.scalars(select(FavoriteItem).where(FavoriteItem.user_id == user_id,
        exists().where(SavedTopic.user_id == user_id, SavedTopic.topic_key == FavoriteItem.topic_key),
    ).order_by(FavoriteItem.created_at.desc(), FavoriteItem.id.desc())).all()
    return {'items': [FavoriteView(fingerprint=i.fingerprint, topicKey=i.topic_key, topicTitle=i.topic_title) for i in rows]}


@router.delete('/favorites/{fingerprint}', status_code=204)
def delete_favorite(fingerprint: str, db: DB, user_id: CurrentUser):
    with db.begin():
        item = db.scalar(select(FavoriteItem).where(FavoriteItem.user_id == user_id, FavoriteItem.fingerprint == fingerprint).with_for_update())
        if item is None:
            raise not_found()
        db.delete(item)
    return Response(status_code=204)
