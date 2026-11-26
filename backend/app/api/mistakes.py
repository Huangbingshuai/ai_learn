"""User-scoped mistake book endpoints."""

import base64
import json
import re
from binascii import Error as Base64Error
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, exists, func, or_, select

from app.api.dependencies import DB, CurrentUser
from app.api.errors import ApiError, ErrorView, not_found
from app.db import MistakeItem, MistakeStatus, SavedTopic, utcnow

router = APIRouter(responses={401: {"model": ErrorView}, 404: {"model": ErrorView}})
StatusValue = Literal["to_review", "mastered"]


class MistakeTopic(BaseModel):
    topicKey: str
    title: str


class MistakeView(BaseModel):
    fingerprint: str
    question: dict
    latestReceipt: dict
    topicKey: str
    topicTitle: str
    wrongCount: int
    firstWrongAt: datetime
    lastWrongAt: datetime
    status: StatusValue
    masteredAt: datetime | None


class MistakePage(BaseModel):
    items: list[MistakeView]
    nextCursor: str | None
    pendingCount: int
    topics: list[MistakeTopic]


class MistakeStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: StatusValue


def _status_value(status: str) -> StatusValue:
    return "mastered" if status == MistakeStatus.MASTERED.value else "to_review"


def _view(item: MistakeItem) -> MistakeView:
    return MistakeView(
        fingerprint=item.fingerprint,
        question=item.question_snapshot,
        latestReceipt=item.latest_receipt,
        topicKey=item.topic_key,
        topicTitle=item.topic_title,
        wrongCount=item.wrong_count,
        firstWrongAt=item.first_wrong_at,
        lastWrongAt=item.last_wrong_at,
        status=_status_value(item.status),
        masteredAt=item.mastered_at,
    )


def _saved_topic_filter(user_id: str):
    return exists(
        select(SavedTopic.topic_key).where(
            SavedTopic.user_id == user_id,
            SavedTopic.topic_key == MistakeItem.topic_key,
        )
    )


def _encode_cursor(item: MistakeItem) -> str:
    value = json.dumps(
        {"at": item.last_wrong_at.isoformat(), "fingerprint": item.fingerprint},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + padding))
        at = datetime.fromisoformat(value["at"])
        fingerprint = value["fingerprint"]
        if at.tzinfo is None or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError
        return at, fingerprint
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError, Base64Error):
        raise ApiError(422, "INVALID_CURSOR", "错题本的翻页位置无效。") from None


@router.get("/mistakes", response_model=MistakePage)
def list_mistakes(
    db: DB,
    user_id: CurrentUser,
    status: Annotated[StatusValue | None, Query()] = None,
    topicKey: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> MistakePage:
    saved_topic = _saved_topic_filter(user_id)
    filters = [MistakeItem.user_id == user_id, saved_topic]
    if status is not None:
        filters.append(MistakeItem.status == status.upper())
    if topicKey is not None:
        filters.append(MistakeItem.topic_key == topicKey)

    statement = select(MistakeItem).where(*filters)
    if cursor is not None:
        last_at, last_fingerprint = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                MistakeItem.last_wrong_at < last_at,
                and_(
                    MistakeItem.last_wrong_at == last_at,
                    MistakeItem.fingerprint < last_fingerprint,
                ),
            )
        )
    items = list(
        db.scalars(
            statement.order_by(
                MistakeItem.last_wrong_at.desc(), MistakeItem.fingerprint.desc()
            ).limit(limit + 1)
        ).all()
    )
    has_more = len(items) > limit
    items = items[:limit]
    count_filters = [MistakeItem.user_id == user_id, saved_topic]
    if status is not None:
        count_filters.append(MistakeItem.status == status.upper())
    pending_count = db.scalar(select(func.count()).select_from(MistakeItem).where(*count_filters))
    topics = db.execute(
        select(SavedTopic.topic_key, SavedTopic.title)
        .join(MistakeItem, MistakeItem.topic_key == SavedTopic.topic_key)
        .where(SavedTopic.user_id == user_id, MistakeItem.user_id == user_id)
        .distinct()
        .order_by(SavedTopic.title, SavedTopic.topic_key)
    ).all()
    return MistakePage(
        items=[_view(item) for item in items],
        nextCursor=_encode_cursor(items[-1]) if has_more and items else None,
        pendingCount=pending_count or 0,
        topics=[MistakeTopic(topicKey=key, title=title) for key, title in topics],
    )


@router.get("/mistakes/{fingerprint}", response_model=MistakeView)
def get_mistake(
    db: DB,
    user_id: CurrentUser,
    fingerprint: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
) -> MistakeView:
    item = db.scalar(
        select(MistakeItem).where(
            MistakeItem.user_id == user_id,
            MistakeItem.fingerprint == fingerprint,
            _saved_topic_filter(user_id),
        )
    )
    if item is None:
        raise not_found()
    return _view(item)


@router.patch("/mistakes/{fingerprint}", response_model=MistakeView)
def update_mistake_status(
    body: MistakeStatusUpdate,
    db: DB,
    user_id: CurrentUser,
    fingerprint: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
) -> MistakeView:
    with db.begin():
        item = db.scalar(
            select(MistakeItem)
            .where(
                MistakeItem.user_id == user_id,
                MistakeItem.fingerprint == fingerprint,
                _saved_topic_filter(user_id),
            )
            .with_for_update()
        )
        if item is None:
            raise not_found()
        item.status = body.status.upper()
        item.mastered_at = utcnow() if body.status == "mastered" else None
        item.updated_at = utcnow()
        db.flush()
        return _view(item)


@router.delete("/mistakes/{fingerprint}", status_code=204)
def remove_mistake(
    db: DB,
    user_id: CurrentUser,
    fingerprint: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
) -> Response:
    with db.begin():
        item = db.scalar(
            select(MistakeItem)
            .where(
                MistakeItem.user_id == user_id,
                MistakeItem.fingerprint == fingerprint,
                _saved_topic_filter(user_id),
            )
            .with_for_update()
        )
        if item is None:
            raise not_found()
        db.delete(item)
    return Response(status_code=204)
