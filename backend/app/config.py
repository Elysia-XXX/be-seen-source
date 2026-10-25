from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")
    database_url: str = "sqlite:///./data/beseen.db"
    demo_password: str = "be-seen-local"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    tencentcloud_secret_id: str = ""
    tencentcloud_secret_key: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    search_limit: int = 40
    model_limit: int = 80
    search_concurrency: int = 3
    model_concurrency: int = 2
    worker_lock: str = "./data/worker.lock"

    @property
    def configured(self):
        return bool(self.base_url and self.api_key and self.model and
                    self.tencentcloud_secret_id and self.tencentcloud_secret_key)


@lru_cache
def settings():
    return Settings()
