import logging
import re
from time import monotonic
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from app.quota import QuotaReached

logger = logging.getLogger(__name__)


class ErrorView(BaseModel):
    code: str
    message: str
    requestId: str
    retryable: bool = False


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable

    def view(self, request_id: str) -> ErrorView:
        return ErrorView(
            code=self.code, message=self.message, requestId=request_id, retryable=self.retryable
        )


def not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "找不到该内容。")


def unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "请重新登录。")


def install_errors(app):
    @app.middleware("http")
    async def request_id(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            incoming if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", incoming) else str(uuid4())
        )
        started = monotonic()
        try:
            response = await call_next(request)
        except Exception as error:  # noqa: BLE001 - normalize unexpected request failures
            logger.error(
                "API request failed request_id=%s exception_type=%s",
                request.state.request_id,
                type(error).__name__,
            )
            problem = ApiError(500, "INTERNAL_ERROR", "服务暂时不可用，请稍后重试。", True)
            response = JSONResponse(problem.view(request.state.request_id).model_dump(), 500)
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info(
            "API request request_id=%s method=%s status=%d duration_ms=%d",
            request.state.request_id,
            request.method,
            response.status_code,
            round((monotonic() - started) * 1000),
        )
        return response

    @app.exception_handler(ApiError)
    async def business_error(request: Request, error: ApiError):
        return JSONResponse(error.view(request.state.request_id).model_dump(), error.status)

    @app.exception_handler(QuotaReached)
    async def quota_error(request: Request, _: QuotaReached):
        error = ApiError(429, "RATE_LIMITED", "请求次数已达上限，请稍后再试。", True)
        return JSONResponse(error.view(request.state.request_id).model_dump(), error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _: RequestValidationError):
        error = ApiError(422, "INVALID_INPUT", "输入不符合要求。")
        return JSONResponse(error.view(request.state.request_id).model_dump(), error.status)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        error = (
            not_found()
            if exc.status_code == 404
            else ApiError(exc.status_code, "HTTP_ERROR", "请求无法处理。")
        )
        return JSONResponse(error.view(request.state.request_id).model_dump(), error.status)


class RequestBodyLimit:
    """Reject oversized streaming requests before parsing JSON."""

    def __init__(self, app, max_bytes: int = 32768):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        payload = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(payload) + len(chunk) > self.max_bytes:
                error = ApiError(413, "REQUEST_TOO_LARGE", "请求内容过长，请缩短后再试。")
                response = JSONResponse(
                    error.view(scope["state"]["request_id"]).model_dump(), error.status
                )
                await response(scope, receive, send)
                return
            payload.extend(chunk)
            if not message.get("more_body", False):
                break

        pending = True

        async def replay():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": bytes(payload), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
