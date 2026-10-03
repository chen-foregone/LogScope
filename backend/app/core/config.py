from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    enabled: bool = False
    model: str = 'qwen-plus'
    base_url: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    api_key: SecretStr | None = Field(default=None, validation_alias='DASHSCOPE_API_KEY')
    budget_microyuan: int = Field(default=10_000_000, ge=1, le=10_000_000)
    input_microyuan_per_million_tokens: int | None = Field(default=None, gt=0)
    output_microyuan_per_million_tokens: int | None = Field(default=None, gt=0)
    price_version: str | None = Field(default=None, min_length=1, max_length=100)
    region_confirmed: bool = False
    max_calls: int = Field(default=4, ge=1, le=6)
    max_output_tokens: int = Field(default=2048, ge=128, le=4096)
    max_input_bytes: int = Field(default=32768, ge=1024, le=65536)
    request_timeout_seconds: int = Field(default=30, ge=1, le=60)

    model_config = SettingsConfigDict(
        env_prefix='AGENT_', env_file='.env', env_file_encoding='utf-8',
        extra='ignore', populate_by_name=True, hide_input_in_errors=True,
    )

    @model_validator(mode='after')
    def validate_access(self):
        # Pin the approved origin: credentials must not follow arbitrary endpoints.
        if self.base_url != 'https://dashscope.aliyuncs.com/compatible-mode/v1':
            raise ValueError('Agent endpoint is not approved')
        if self.model != 'qwen-plus':
            raise ValueError('Agent model is not approved')
        if self.enabled and (
            self.api_key is None or not self.api_key.get_secret_value().strip()
            or self.input_microyuan_per_million_tokens is None
            or self.output_microyuan_per_million_tokens is None
            or not self.price_version or not self.price_version.strip()
            or not self.region_confirmed
        ):
            raise ValueError('Agent requires credentials, confirmed region and verified pricing')
        return self


class Settings(BaseSettings):
    app_name: str = "LogScope API"
    database_url: str = "postgresql+psycopg://logscope:logscope@localhost:5432/logscope"
    api_prefix: str = "/api"
    services_file: str | None = None
    executor_credential: str | None = None
    simulated_logs_url: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
