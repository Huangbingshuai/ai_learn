import re
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException


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
        request.state.request_id = incoming if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", incoming) else str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def business_error(request: Request, error: ApiError):
        return JSONResponse(error.view(request.state.request_id).model_dump(), error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _: RequestValidationError):
        error = ApiError(422, "INVALID_INPUT", "输入不符合要求。")
        return JSONResponse(error.view(request.state.request_id).model_dump(), error.status)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        error = not_found() if exc.status_code == 404 else ApiError(
            exc.status_code, "HTTP_ERROR", "请求无法处理。"
        )
        return JSONResponse(error.view(request.state.request_id).model_dump(), error.status)
