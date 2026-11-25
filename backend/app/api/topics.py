from fastapi import APIRouter
from pydantic import BaseModel

from app.api.dependencies import CurrentUser
from app.api.errors import ApiError, ErrorView
from app.domain.topics import TopicView, load_topics

router = APIRouter(responses={401: {"model": ErrorView}, 503: {"model": ErrorView}})


class TopicList(BaseModel):
    items: list[TopicView]


@router.get("/topics")
def list_topics(_user_id: CurrentUser) -> TopicList:
    try:
        return TopicList(items=load_topics())
    except ValueError:
        raise ApiError(503, "SOURCE_INSUFFICIENT", "学习主题暂时不可用。") from None
