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
    ai_provider: Literal["qwen", "deepseek"] = "qwen"
    dashscope_api_key: SecretStr = SecretStr("")
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-flash"
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_model: str = "deepseek-flash"
    tavily_api_key: SecretStr = SecretStr("")
    search_timeout: float = Field(default=15, gt=0, le=40)
    model_timeout: float = Field(default=100, gt=0)
    user_daily_generation_limit: int = Field(default=8, gt=0)
    global_daily_generation_limit: int = Field(default=500, gt=0)
    user_daily_summary_limit: int = Field(default=24, gt=0)
    global_daily_summary_limit: int = Field(default=1200, gt=0)
    quiz_hours: int = Field(default=24, gt=0)
    job_minutes: int = Field(default=25, gt=0)
    worker_poll_seconds: float = Field(default=1, gt=0)
    max_generation_calls: int = Field(default=7, ge=4)
    max_summary_calls: int = Field(default=4, ge=2)

    @property
    def ai_model(self) -> str:
        """The model recorded with generated jobs and summaries."""
        return self.qwen_model if self.ai_provider == "qwen" else self.deepseek_model
