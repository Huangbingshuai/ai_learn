import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.api.errors import ApiError
from app.config import Settings
from app.main import create_app

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def fixed_data():
    source = BACKEND / "data" / "topics" / "fixed-seasons.json"
    return json.loads(source.read_text())


@pytest.fixture
def database():
    database_url = os.getenv(
        "QINGZHI_TEST_DATABASE_URL",
        "postgresql+psycopg://qingzhi:qingzhi-local@127.0.0.1:55432/qingzhi_test",
    )
    schema = f"test_{uuid4().hex}"
    control = create_engine(database_url)
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        config = Config(str(BACKEND / "alembic.ini"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


@pytest.fixture
def settings():
    return Settings(_env_file=None, app_env="development", wechat_app_id="test-wechat")


def stub_exchange(_settings, code):
    if code == "bad":
        raise ApiError(401, "WECHAT_CODE_INVALID", "凭证无效。")
    if code == "slow":
        raise ApiError(503, "WECHAT_UNAVAILABLE", "微信暂时不可用。", True)
    return f"openid-{code}"


@pytest.fixture
def client(database, settings):
    with TestClient(create_app(settings, database, stub_exchange)) as client:
        yield client


@pytest.fixture
def login(client):
    def create(device="device-123456"):
        response = client.post("/v1/dev/auth", json={"deviceId": device})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['accessToken']}"}

    return create
