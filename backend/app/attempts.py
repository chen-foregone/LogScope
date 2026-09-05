import json
from contextlib import contextmanager
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, Table, Text, Uuid, insert, select, update

from app.investigations import InvestigationView, investigations, metadata, representation

attempts = Table(
    "attempts", metadata,
    Column("id", Uuid, primary_key=True),
    Column("investigation_id", Uuid, ForeignKey("investigations.id")),
    Column("request_key", Uuid), Column("request_body", Text),
    Column("service_id", String(80)), Column("source_ids", JSON),
    Column("starts_at", DateTime(timezone=True)), Column("ends_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)), Column("status", String(20)),
    Column("evidence_unavailable", Boolean),
    Column("ended_at", DateTime(timezone=True)), Column("result", String(24)), Column("end_reason", String(32)),
)
executions = Table(
    "controlled_executions", metadata,
    Column("id", Uuid, primary_key=True),
    Column("attempt_id", Uuid, ForeignKey("attempts.id"), unique=True),
    Column("valid", Boolean),
)


class StartAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_id: str = Field(min_length=1, max_length=80)
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None

    @model_validator(mode="before")
    @classmethod
    def absolute_strings(cls, values):
        if isinstance(values, dict):
            for field in ("starts_at", "ends_at"):
                if field in values and (not isinstance(values[field], str) or 'T' not in values[field]):
                    raise ValueError("显式时间必须为带时区的绝对时间字符串；默认最近1小时请省略两个时间字段")
        return values

    @model_validator(mode="after")
    def valid_range(self):
        if (self.starts_at is None) != (self.ends_at is None):
            raise ValueError("请同时提供带时区的开始与结束时间，或均省略使用服务器最近1小时")
        if self.starts_at is not None and not timedelta(0) < self.ends_at - self.starts_at <= timedelta(hours=24):
            raise ValueError("日志范围必须开始早于结束，且跨度不超过24小时")
        return self


def attempt_views(connection, investigation_id):
    rows = connection.execute(select(attempts).where(attempts.c.investigation_id == investigation_id)
                              .order_by(attempts.c.created_at, attempts.c.id)).mappings().all()
    from app.evidence import evidence_views
    views = []
    for row in rows:
        saved = evidence_views(connection, row['id'])
        notices = ['部分材料不可用：不符合受控输入契约，未保存原文、未纳入分析。'] if row['evidence_unavailable'] else []
        if any(item['identity_status'] != 'reliable' for item in saved):
            notices.append('可能重复：部分证据缺少可靠原始日志身份，已逐条保留，未按内容或时间合并。')
        if any(item['identity_status'] == 'legacy_unknown' for item in saved):
            notices.append('升级前证据未声明可靠身份：历史记录原样保留，未倒推身份或合并。')
        from app.conclusions import conclusion_views
        history = conclusion_views(connection, row['id'])
        from app.queries import query_views, query_coverage, coverage_aliases
        queried = query_views(connection, row['id'])
        views.append({key: row[key] for key in ('id', 'service_id', 'starts_at', 'ends_at', 'created_at', 'status', 'ended_at', 'result', 'end_reason')}
            | {'execution_mode': 'controlled', 'evidence': saved, 'evidence_count': len(saved), 'evidence_notices': notices,
               'conclusions': history, 'conclusion': history[-1] if history else None,
               'queries': queried, 'query_coverage': query_coverage(queried, coverage_aliases(connection, row['id'], row['source_ids']))})
    return views


def current_view(connection, row):
    return representation(row, attempt_views(connection, row["id"]))


class ControlledExecutionAdapter:
    """仅持久接纳，不启动线程、不接日志或Agent；接纳与业务共享提交/回滚。"""

    def accept(self, connection, attempt_id):
        connection.execute(insert(executions).values(id=uuid4(), attempt_id=attempt_id, valid=True))


@contextmanager
def start_transaction(engine):
    try:
        with engine.begin() as connection:
            yield connection
    except SQLAlchemyError:
        raise HTTPException(503, "数据库启动结果未确认；请使用同一请求标识重试以读取真实结果") from None


def attempt_router(engine, services, adapter):
    router = APIRouter(prefix="/investigations")

    @router.post("/{investigation_id}/attempts", status_code=201, response_model=InvestigationView)
    def start(investigation_id: UUID, payload: StartAttempt, response: Response,
              idempotency_key: Annotated[UUID, Header()]):
        body = json.dumps(payload.model_dump(mode="json"), sort_keys=True)
        with start_transaction(engine) as connection:
            row = connection.execute(select(investigations).where(investigations.c.id == investigation_id)
                                     .with_for_update()).mappings().first()
            if row is None:
                raise HTTPException(404, "调查不存在")
            replay = connection.execute(select(attempts).where(
                attempts.c.investigation_id == investigation_id, attempts.c.request_key == idempotency_key)).mappings().first()
            if replay:
                if replay["request_body"] != body:
                    raise HTTPException(409, "启动请求标识已用于不同范围")
                response.status_code = 200
                return current_view(connection, row)
            if connection.execute(select(attempts.c.id).where(
                    attempts.c.investigation_id == investigation_id, attempts.c.status == "调查中")).first():
                raise HTTPException(409, "已有调查中轮次，不能修改服务或范围，也不能重复启动")
            if connection.execute(select(attempts.c.id).where(attempts.c.investigation_id == investigation_id)).first():
                raise HTTPException(409, "历史轮次已结束；手动新轮契约由后续工单09提供")
            service = services.get(payload.service_id)
            if service is None:
                raise HTTPException(422, "服务未配置或无效，尚未启动")
            now = datetime.now(timezone.utc)
            attempt_id = uuid4()
            connection.execute(insert(attempts).values(
                id=attempt_id, investigation_id=investigation_id, request_key=idempotency_key,
                request_body=body, service_id=service.id, source_ids=service.source_ids,
                starts_at=payload.starts_at or now - timedelta(hours=1), ends_at=payload.ends_at or now,
                created_at=now, status="调查中"))
            from app.evidence import redaction_keys
            import secrets
            if connection.execute(select(redaction_keys.c.secret).where(
                    redaction_keys.c.investigation_id == investigation_id)).first() is None:
                connection.execute(insert(redaction_keys).values(investigation_id=investigation_id, secret=secrets.token_bytes(32)))
            try:
                adapter.accept(connection, attempt_id)
            except Exception:
                raise HTTPException(503, "可控执行接纳失败，尚未启动；可使用同一请求标识重试") from None
            row = connection.execute(update(investigations).where(investigations.c.id == investigation_id)
                                     .values(updated_at=now, retention_expires_at=None)
                                     .returning(investigations)).mappings().one()
            result = current_view(connection, row)
        return result

    @router.get("/{investigation_id}/attempts/{attempt_id}")
    def detail(investigation_id: UUID, attempt_id: UUID):
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            views = attempt_views(connection, investigation_id)
            for view in views:
                if view["id"] == attempt_id:
                    return view
        raise HTTPException(404, "调查轮次不存在")

    return router
