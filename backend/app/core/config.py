from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "LogScope API"
    database_url: str = "postgresql+psycopg://logscope:logscope@localhost:5432/logscope"
    api_prefix: str = "/api"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
