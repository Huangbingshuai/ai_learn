import logging
import secrets
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert

from app.api.dependencies import DB, digest
from app.api.errors import ApiError, ErrorView
from app.config import Settings
from app.db import SessionRow, User, utcnow
from app.quota import reserve_quota

# HTTPX logs complete URLs at INFO; the WeChat API requires credentials in the query.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class WechatLogin(BaseModel):
    code: str = Field(min_length=1, max_length=512)


class DeviceLogin(BaseModel):
    deviceId: str = Field(min_length=12, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class LoginView(BaseModel):
    accessToken: str
    expiresAt: datetime
    userId: str


def wechat_unavailable() -> ApiError:
    return ApiError(503, "WECHAT_UNAVAILABLE", "微信登录暂时不可用，请稍后重试。", True)


def exchange_wechat_code(settings: Settings, code: str) -> str:
    secret = settings.wechat_app_secret.get_secret_value()
    if not settings.wechat_app_id or not secret:
        raise ApiError(503, "WECHAT_NOT_CONFIGURED", "微信登录尚未配置。")
    try:
        response = httpx.get(
            "https://api.weixin.qq.com/sns/jscode2session",
            params={
                "appid": settings.wechat_app_id,
                "secret": secret,
                "js_code": code,
                "grant_type": "authorization_code",
            },
            timeout=settings.wechat_timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        raise wechat_unavailable() from None
    if not isinstance(payload, dict):
        raise wechat_unavailable()
    if payload.get("errcode") in (40029, 40163):
        raise ApiError(401, "WECHAT_CODE_INVALID", "微信登录凭证已失效，请重新登录。")
    if payload.get("errcode") == 45011:
        raise ApiError(429, "RATE_LIMITED", "登录太频繁，请稍后再试。", True)
    openid = payload.get("openid")
    if payload.get("errcode") or not isinstance(openid, str) or not 1 <= len(openid) <= 128:
        raise wechat_unavailable()
    return openid


def issue_session(db, app_id: str, openid: str) -> LoginView:
    with db.begin():
        user_id = db.scalar(
            insert(User)
            .values(app_id=app_id, openid=openid)
            .on_conflict_do_update(constraint="uq_users_wechat_identity", set_={"openid": openid})
            .returning(User.id)
        )
        created = utcnow()
        expires = created + timedelta(hours=12)
        token = secrets.token_urlsafe(32)
        db.add(
            SessionRow(
                user_id=user_id, token_digest=digest(token), created_at=created, expires_at=expires
            )
        )
    return LoginView(accessToken=token, expiresAt=expires, userId=user_id)


def limit_login(db, request: Request) -> None:
    now = utcnow()
    start = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0)
    address = request.client.host if request.client else "unknown"
    with db.begin():
        reserve_quota(
            db,
            f"login:{address}",
            start,
            start + timedelta(minutes=5),
            request.app.state.settings.login_limit,
        )


router = APIRouter(responses={422: {"model": ErrorView}, 429: {"model": ErrorView}})
local_router = APIRouter(responses={422: {"model": ErrorView}, 429: {"model": ErrorView}})


@router.post("/auth/wechat", responses={401: {"model": ErrorView}, 503: {"model": ErrorView}})
def login_by_wechat(body: WechatLogin, db: DB, request: Request) -> LoginView:
    settings = request.app.state.settings
    limit_login(db, request)
    openid = request.app.state.wechat_exchange(settings, body.code)
    return issue_session(db, settings.wechat_app_id, openid)


@local_router.post("/dev/auth")
def login_by_device(body: DeviceLogin, db: DB, request: Request) -> LoginView:
    limit_login(db, request)
    return issue_session(db, "qingzhi-development", body.deviceId)
