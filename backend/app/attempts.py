import json
from contextlib import contextmanager
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Table, Text, Uuid, func, insert, select, update

from app.investigations import InvestigationView, investigations, metadata, representation

instance_admission = Table('instance_admission', metadata, Column('id', Integer, primary_key=True))

attempts = Table(
    "attempts", metadata,
    Column("id", Uuid, primary_key=True),
    Column("investigation_id", Uuid, ForeignKey("investigations.id")),
    Column("request_key", Uuid), Column("request_body", Text),
    Column("attempt_number", Integer),
    Column("service_id", String(80)), Column("source_ids", JSON),
    Column("starts_at", DateTime(timezone=True)), Column("ends_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)), Column("status", String(20)),
    Column("evidence_unavailable", Boolean),
    Column("deadline_at", DateTime(timezone=True)),
    Column("timeout_requested_at", DateTime(timezone=True)),
    Column("lost_requested_at", DateTime(timezone=True)),
    Column("interruption_confirmation", JSON),
    Column("cancel_requested_at", DateTime(timezone=True)),
    Column("stop_confirmed_at", DateTime(timezone=True)), Column("stop_confirmation", String(64)),
    Column("ended_at", DateTime(timezone=True)), Column("result", String(24)), Column("end_reason", String(32)),
)
executions = Table(
    "controlled_executions", metadata,
    Column("id", Uuid, primary_key=True),
    Column("attempt_id", Uuid, ForeignKey("attempts.id"), unique=True),
    Column("valid", Boolean), Column("stop_protocol", String(40)),
    Column("process_identity", JSON),
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
                              .order_by(attempts.c.attempt_number)).mappings().all()
    from app.evidence import evidence_views, historical_reference_states
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
        views.append({key: row[key] for key in ('id', 'attempt_number', 'service_id', 'starts_at', 'ends_at', 'created_at', 'deadline_at', 'status', 'ended_at', 'result', 'end_reason')}
            | {'execution_mode': 'controlled', 'evidence': saved, 'evidence_count': len(saved), 'evidence_notices': notices,
               'historical_reference_states': historical_reference_states(connection, row),
               'conclusions': history, 'conclusion': history[-1] if history else None,
               'queries': queried, 'query_coverage': query_coverage(queried, coverage_aliases(connection, row['id'], row['source_ids'])),
               'cancel_requested_at': row['cancel_requested_at'], 'stop_confirmed_at': row['stop_confirmed_at'],
               'stop_confirmation': row['stop_confirmation'], 'timeout_requested_at': row['timeout_requested_at'],
               'lost_requested_at': row['lost_requested_at'], 'interruption_confirmation': row['interruption_confirmation'],
               'execution_phase': 'stopped' if row['status'] != '调查中' else ('confirming_execution' if row['lost_requested_at'] else 'stopping' if row['cancel_requested_at'] else 'timeout_stopping' if row['timeout_requested_at'] else 'running')})
    for view in views:
        for key, value in view.items():
            if isinstance(value, datetime):
                view[key] = value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    return views


def current_view(connection, row):
    from app.severity import severity_view
    views = attempt_views(connection, row['id'])
    history, suggestions = severity_view(connection, row['id'], row['severity_version'], views[-1] if views else None)
    return representation(row, views) | {'severity_history': history, 'severity_suggestions': suggestions}


class ControlledExecutionAdapter:
    """仅持久接纳，不启动线程、不接日志或Agent；接纳与业务共享提交/回滚。"""

    def accept(self, connection, attempt_id):
        from app.processes import current_process
        connection.execute(insert(executions).values(id=uuid4(), attempt_id=attempt_id, valid=True,
            process_identity=current_process() if type(self) is ControlledExecutionAdapter else None,
            stop_protocol='controlled_queries_v1' if type(self) is ControlledExecutionAdapter else None))


@contextmanager
def start_transaction(engine):
    try:
        with engine.connect().execution_options(isolation_level='READ COMMITTED') as connection, connection.begin():
            yield connection
    except SQLAlchemyError:
        raise HTTPException(503, "数据库启动结果未确认；请使用同一请求标识重试以读取真实结果") from None


def attempt_router(engine, services, adapter, clock):
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
            from app.queries import queries
            if connection.execute(select(queries.c.id).join(attempts, attempts.c.id == queries.c.attempt_id).where(
                    attempts.c.investigation_id == investigation_id, queries.c.active.is_(True))).first():
                raise HTTPException(409, "旧轮查询尚未确认退出，不能启动新轮")
            service = services.get(payload.service_id)
            if service is None:
                raise HTTPException(422, "服务未配置或无效，尚未启动")
            # 调查→接纳锁；计数独立SQL在锁等待后取得新快照，不维护可漂移的人工计数。
            connection.execute(select(instance_admission.c.id).where(instance_admission.c.id == 1)
                               .with_for_update()).scalar_one()
            if connection.execute(select(func.count()).select_from(attempts).where(
                    attempts.c.status == '调查中')).scalar_one() >= 3:
                raise HTTPException(409, '实例已满额（最多3个调查）；尚未启动，不排队，请稍后手动重试')
            now = clock()
            attempt_id = uuid4()
            previous_number = connection.execute(select(attempts.c.attempt_number).where(
                attempts.c.investigation_id == investigation_id).order_by(attempts.c.attempt_number.desc()).limit(1)).scalar_one_or_none()
            connection.execute(insert(attempts).values(
                id=attempt_id, investigation_id=investigation_id, request_key=idempotency_key,
                attempt_number=(previous_number or 0) + 1,
                request_body=body, service_id=service.id, source_ids=service.source_ids,
                starts_at=payload.starts_at or now - timedelta(hours=1), ends_at=payload.ends_at or now,
                created_at=now, deadline_at=now + timedelta(minutes=10), status="调查中"))
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
