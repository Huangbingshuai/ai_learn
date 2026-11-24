from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "production"] = "production"
    database_url: str = "postgresql+psycopg://qingzhi:qingzhi-local@127.0.0.1:55432/qingzhi"
    wechat_app_id: str = ""
    wechat_app_secret: SecretStr = SecretStr("")
    wechat_timeout: float = Field(default=8, gt=0)
    login_limit: int = Field(default=60, gt=0)
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_model: str = "deepseek-flash"
    model_timeout: float = Field(default=100, gt=0)
    user_daily_generation_limit: int = Field(default=8, gt=0)
    global_daily_generation_limit: int = Field(default=500, gt=0)
    quiz_hours: int = Field(default=24, gt=0)
    job_minutes: int = Field(default=25, gt=0)
    worker_poll_seconds: float = Field(default=1, gt=0)
    max_generation_calls: int = Field(default=7, ge=4)
    max_summary_calls: int = Field(default=4, ge=2)
