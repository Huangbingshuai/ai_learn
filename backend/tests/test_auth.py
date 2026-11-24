from datetime import timedelta
from importlib.util import find_spec

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select


def test_app_is_available():
    assert find_spec("app.main") is not None


def test_health_and_request_id(client):
    response = client.get("/healthz", headers={"X-Request-ID": "test-request"})
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "test-request"


def test_development_login_keeps_only_token_digest(client, database):
    from app.db import SessionRow, User

    first = client.post("/v1/dev/auth", json={"deviceId": "device-123456"}).json()
    second = client.post("/v1/dev/auth", json={"deviceId": "device-123456"}).json()
    assert first["accessToken"] != second["accessToken"]
    with database() as session:
        assert len(session.scalars(select(User)).all()) == 1
        rows = session.scalars(select(SessionRow)).all()
        assert len(rows) == 2
        assert first["accessToken"] not in {row.token_digest for row in rows}
        assert rows[0].expires_at - rows[0].created_at == timedelta(hours=12)


@pytest.mark.parametrize("code", ["good", "other"])
def test_wechat_login_keeps_provider_identity_private(client, code):
    login = client.post("/v1/auth/wechat", json={"code": code})
    assert login.status_code == 200
    assert set(login.json()) == {"accessToken", "expiresAt"}


@pytest.mark.parametrize("code,status,error", [("bad", 401, "WECHAT_CODE_INVALID"), ("slow", 503, "WECHAT_UNAVAILABLE")])
def test_wechat_failure_has_public_error_shape(client, code, status, error):
    result = client.post("/v1/auth/wechat", json={"code": code}, headers={"X-Request-ID": "abc"})
    assert result.status_code == status
    assert result.json()["code"] == error
    assert result.json()["requestId"] == "abc"
    assert result.json()["retryable"] is (status == 503)


def test_production_disables_local_login(database):
    from app.config import Settings
    from app.main import create_app

    with TestClient(create_app(Settings(app_env="production"), database)) as client:
        result = client.post("/v1/dev/auth", json={"deviceId": "device-123456"})
    assert result.status_code == 404


def test_validation_does_not_echo_request_body(client):
    response = client.post("/v1/auth/wechat", json={"code": "private-sentinel" * 100})
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_INPUT"
    assert "private-sentinel" not in response.text


def test_expired_bearer_cannot_access_resources(database, settings):
    from fastapi import Depends
    from sqlalchemy import update

    from app.api.dependencies import get_user
    from app.db import SessionRow, utcnow
    from app.main import create_app

    application = create_app(settings, database)

    @application.get("/protected")
    def protected(user_id=Depends(get_user)):
        return {"id": user_id}

    with TestClient(application) as client:
        login = client.post("/v1/dev/auth", json={"deviceId": "device-123456"}).json()
        header = {"Authorization": f"Bearer {login['accessToken']}"}
        assert client.get("/protected", headers=header).status_code == 200
        with database.begin() as db:
            db.execute(update(SessionRow).values(expires_at=utcnow() - timedelta(seconds=1)))
        assert client.get("/protected", headers=header).status_code == 401


@pytest.mark.parametrize(
    "payload, status, expected",
    [
        ({"openid": "identity", "session_key": "private"}, 200, "identity"),
        ({"errcode": 40029}, 401, "WECHAT_CODE_INVALID"),
        ({"errcode": 45011}, 429, "RATE_LIMITED"),
        ({"errcode": 40013}, 503, "WECHAT_UNAVAILABLE"),
        ([], 503, "WECHAT_UNAVAILABLE"),
    ],
)
def test_wechat_exchange_handles_provider_payload(monkeypatch, payload, status, expected):
    import httpx

    from app.api.auth import exchange_wechat_code
    from app.api.errors import ApiError
    from app.config import Settings

    def respond(url, *, params, timeout):
        assert url == "https://api.weixin.qq.com/sns/jscode2session"
        assert params == {
            "appid": "app",
            "secret": "test-only",
            "js_code": "test-code",
            "grant_type": "authorization_code",
        }
        assert timeout == 8
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", respond)
    config = Settings(wechat_app_id="app", wechat_app_secret="test-only")
    if status == 200:
        assert exchange_wechat_code(config, "test-code") == expected
    else:
        with pytest.raises(ApiError) as error:
            exchange_wechat_code(config, "test-code")
        assert (error.value.status, error.value.code) == (status, expected)


def test_wechat_exchange_timeout_is_sanitized(monkeypatch):
    import httpx

    from app.api.auth import exchange_wechat_code
    from app.api.errors import ApiError
    from app.config import Settings

    def timeout(*_, **__):
        raise httpx.ReadTimeout("raw secret or code")

    monkeypatch.setattr(httpx, "get", timeout)
    with pytest.raises(ApiError) as error:
        exchange_wechat_code(Settings(wechat_app_id="app", wechat_app_secret="test-only"), "code")
    assert error.value.code == "WECHAT_UNAVAILABLE"
    assert "raw secret" not in str(error.value)
