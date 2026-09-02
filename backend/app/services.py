import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Service(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=120)
    source_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_sources(self):
        if any(not source.strip() or len(source) > 120 or
               any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in source)
               for source in self.source_ids):
            raise ValueError("来源标识不能为空、包含控制字符或超过120字符")
        if any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in self.name):
            raise ValueError("服务名称包含非法字符")
        return self


class ServiceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    services: list[Service] = []

    @model_validator(mode="after")
    def unique_services(self):
        if len({service.id for service in self.services}) != len(self.services):
            raise ValueError("服务标识不得重复")
        return self


def load_services(path: str | Path | None):
    if not path:
        return {}
    try:
        catalog = ServiceCatalog.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        raise ValueError("服务目录配置无效，请检查后端配置文件") from None
    return {service.id: service for service in catalog.services}


def service_router(services):
    router = APIRouter()

    @router.get("/services")
    def listing():
        return {"items": [{"id": service.id, "name": service.name} for service in services.values()]}

    return router
