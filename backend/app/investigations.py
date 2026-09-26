from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Response
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Column, DateTime, MetaData, String, Table, Uuid, Integer, select

from app.investigation_listing import (InvestigationPageQuery, InvestigationFilterQuery, InvestigationPage,
    InvestigationOverviewResponse, read_investigation_listing)

from app.retention import RetentionView

metadata = MetaData()
investigations = Table(
    "investigations", metadata,
    Column("id", Uuid, primary_key=True),
    Column("request_key", Uuid, unique=True),
    Column("severity", String(20)), Column("severity_source", String(20)),
    Column("severity_assessed_at", DateTime(timezone=True)),
    Column("severity_version", Integer),
    Column("title", String(200), nullable=False),
    Column("description", String(4000), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("retention_expires_at", DateTime(timezone=True), nullable=True),
)


class CreateInvestigation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)

    @field_validator("title", "description")
    @classmethod
    def valid_text(cls, value: str) -> str:
        if any(char == "\x00" or 0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("文本包含不可保存的字符")
        return value


class InvestigationView(BaseModel):
    id: UUID
    title: str
    description: str
    status: Literal["待处理", "调查中", "已完成", "失败", "已取消"]
    severity: Literal["未评估", "高", "中", "低"]
    severity_source: Literal['human', 'agent'] | None
    severity_assessed_at: datetime | None
    severity_version: int
    severity_history: list[dict]
    severity_suggestions: list[dict]
    service: str | None
    attempts: list[dict]
    evidence_count: int
    created_at: datetime
    updated_at: datetime
    retention_expires_at: datetime | None
    retention: RetentionView


class InvestigationList(BaseModel):
    items: list[InvestigationView]
    total: int


def representation(row, attempts=None):
    attempts = attempts or []
    return {
        **{key: value for key, value in row.items() if key != "request_key"}, "id": str(row["id"]),
        **{key: row[key].astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if row[key] else None
           for key in ("created_at", "updated_at", "retention_expires_at")},
        "status": attempts[-1]["status"] if attempts else "待处理",
        "service": attempts[-1]["service_id"] if attempts else None,
        "attempts": attempts, "evidence_count": len(attempts[-1]['evidence']) if attempts else 0,
    }


def investigation_router(engine, clock):
    from app.attempts import current_view
    router = APIRouter(prefix="/investigations")

    @router.post("", status_code=201, response_model=InvestigationView)
    def create(payload: CreateInvestigation, response: Response,
               idempotency_key: Annotated[UUID, Header()]):
        now = clock()
        values = dict(id=uuid4(), request_key=idempotency_key, **payload.model_dump(),
                      created_at=now, updated_at=now, retention_expires_at=now + timedelta(days=30))
        with engine.begin() as connection:
            row = connection.execute(
                pg_insert(investigations).values(**values)
                .on_conflict_do_nothing(index_elements=[investigations.c.request_key])
                .returning(investigations)
            ).mappings().first()
            if row is None:
                # 与start使用同一调查行排他锁，保证后续轮次投影不跨启动提交。
                row = connection.execute(select(investigations).where(
                    investigations.c.request_key == idempotency_key).with_for_update()).mappings().one()
                if any(row[key] != value for key, value in payload.model_dump().items()):
                    raise HTTPException(409, "请求标识已用于不同创建内容")
                response.status_code = 200
            result = current_view(connection, row, clock())
        return result

    @router.get("", response_model=InvestigationPage)
    def listing(filters: Annotated[InvestigationPageQuery, Query()]):
        return read_investigation_listing(engine, filters, clock)

    @router.get("/overview", response_model=InvestigationOverviewResponse)
    def overview(filters: Annotated[InvestigationFilterQuery, Query()]):
        return read_investigation_listing(engine, filters, clock, overview_only=True)

    @router.get("/{investigation_id}", response_model=InvestigationView)
    def detail(investigation_id: UUID):
        # 调查字段与各轮次投影必须来自同一快照；不改变启动等写事务隔离。
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            row = connection.execute(select(investigations).where(investigations.c.id == investigation_id)).mappings().first()
            if row is None:
                raise HTTPException(404, "调查不存在")
            result = current_view(connection, row, clock())
        return result

    return router
