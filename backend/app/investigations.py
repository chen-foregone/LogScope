from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Response
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Column, DateTime, MetaData, String, Table, Uuid, cast, or_, select

metadata = MetaData()
investigations = Table(
    "investigations", metadata,
    Column("id", Uuid, primary_key=True),
    Column("request_key", Uuid, unique=True),
    Column("title", String(200), nullable=False),
    Column("description", String(4000), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("retention_expires_at", DateTime(timezone=True), nullable=False),
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
    status: Literal["待处理"]
    severity: Literal["未评估"]
    service: None
    attempts: list[dict]
    evidence_count: Literal[0]
    created_at: datetime
    updated_at: datetime
    retention_expires_at: datetime


class InvestigationList(BaseModel):
    items: list[InvestigationView]
    total: int


def representation(row):
    return {
        **{key: value for key, value in row.items() if key != "request_key"}, "id": str(row["id"]),
        **{key: row[key].astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
           for key in ("created_at", "updated_at", "retention_expires_at")},
        "status": "待处理", "severity": "未评估", "service": None,
        "attempts": [], "evidence_count": 0,
    }


def investigation_router(engine):
    router = APIRouter(prefix="/investigations")

    @router.post("", status_code=201, response_model=InvestigationView)
    def create(payload: CreateInvestigation, response: Response,
               idempotency_key: Annotated[UUID, Header()]):
        now = datetime.now(timezone.utc)
        values = dict(id=uuid4(), request_key=idempotency_key, **payload.model_dump(),
                      created_at=now, updated_at=now, retention_expires_at=now + timedelta(days=30))
        with engine.begin() as connection:
            row = connection.execute(
                pg_insert(investigations).values(**values)
                .on_conflict_do_nothing(index_elements=[investigations.c.request_key])
                .returning(investigations)
            ).mappings().first()
            if row is None:
                row = connection.execute(select(investigations).where(
                    investigations.c.request_key == idempotency_key)).mappings().one()
                if any(row[key] != value for key, value in payload.model_dump().items()):
                    raise HTTPException(409, "请求标识已用于不同创建内容")
                response.status_code = 200
        return representation(row)

    @router.get("", response_model=InvestigationList)
    def listing(q: str = ""):
        statement = select(investigations)
        query = q.strip()
        if query:
            statement = statement.where(or_(
                investigations.c.title.icontains(query, autoescape=True),
                cast(investigations.c.id, String).icontains(query, autoescape=True),
            ))
        with engine.connect() as connection:
            rows = connection.execute(statement.order_by(investigations.c.updated_at.desc(), investigations.c.id)).mappings().all()
        return {"items": [representation(row) for row in rows], "total": len(rows)}

    @router.get("/{investigation_id}", response_model=InvestigationView)
    def detail(investigation_id: UUID):
        with engine.connect() as connection:
            row = connection.execute(select(investigations).where(investigations.c.id == investigation_id)).mappings().first()
        if row is None:
            raise HTTPException(404, "调查不存在")
        return representation(row)

    return router
