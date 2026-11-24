from fastapi import FastAPI

from app.api import attempts
from app.api.auth import exchange_wechat_code, local_router, router
from app.api.errors import install_errors
from app.config import Settings
from app.db import make_session_factory


def create_app(settings: Settings | None = None, session_factory=None, wechat_exchange=None) -> FastAPI:
    settings = settings or Settings()
    api = FastAPI(title="轻知闯关 API", version="1.0.0")
    api.state.settings = settings
    api.state.session_factory = session_factory or make_session_factory(settings.database_url)
    api.state.wechat_exchange = wechat_exchange or exchange_wechat_code
    install_errors(api)
    api.include_router(router, prefix="/v1")
    api.include_router(attempts.router, prefix="/v1")
    if settings.app_env == "development":
        api.include_router(local_router, prefix="/v1")
        api.include_router(attempts.local_router, prefix="/v1")

    @api.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return api


app = create_app()
