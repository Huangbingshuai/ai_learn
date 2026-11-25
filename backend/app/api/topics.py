from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select, tuple_

from app.api.dependencies import DB, CurrentUser
from app.api.errors import ApiError, ErrorView
from app.db import SavedTopic
from app.domain.topics import TopicView, load_topics

router = APIRouter(responses={401: {"model": ErrorView}, 422: {"model": ErrorView}})


class TopicList(BaseModel):
    items: list[TopicView]
    nextCursor: str | None = None


@router.get("/topics", response_model_exclude_none=True)
def list_topics(
    db: DB,
    user_id: CurrentUser,
    cursor: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> TopicList:
    statement = select(SavedTopic).where(SavedTopic.user_id == user_id)
    if cursor is not None:
        try:
            stamp, key = cursor.split("|")
            updated_at = datetime.fromisoformat(stamp)
            if updated_at.tzinfo is None or not key:
                raise ValueError("Invalid cursor")
        except ValueError:
            raise ApiError(422, "INVALID_INPUT", "主题列表分页已失效，请重新加载。") from None
        statement = statement.where(
            tuple_(SavedTopic.updated_at, SavedTopic.topic_key) < (updated_at, key)
        )
    rows = db.scalars(
        statement.order_by(SavedTopic.updated_at.desc(), SavedTopic.topic_key.desc()).limit(
            limit + 1
        )
    ).all()
    items = [
        TopicView(topicKey=row.topic_key, title=row.title, art="topic") for row in rows[:limit]
    ]
    if cursor is None:
        # Initial examples are available to each session without writing shared saved topics.
        try:
            items.extend(load_topics())
        except ValueError:
            pass
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = f"{last.updated_at.isoformat()}|{last.topic_key}"
    return TopicList(items=items, nextCursor=next_cursor)
