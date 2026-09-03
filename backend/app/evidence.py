import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Column, DateTime, ForeignKey, JSON, LargeBinary, Table, Uuid, String, UniqueConstraint, insert, select, update

from app.attempts import attempts, executions
from app.investigations import investigations, metadata

redaction_keys = Table('investigation_redaction_keys', metadata,
    Column('investigation_id', Uuid, ForeignKey('investigations.id'), primary_key=True),
    Column('secret', LargeBinary, nullable=False))
evidence = Table('evidence', metadata,
    Column('id', Uuid, primary_key=True),
    Column('attempt_id', Uuid, ForeignKey('attempts.id'), nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False), Column('material', JSON, nullable=False),
    Column('identity_key', String(64)), Column('content_key', String(64)),
    Column('identity_status', String(24), nullable=False),
    UniqueConstraint('attempt_id', 'identity_key', name='uq_evidence_attempt_identity'))

CREDENTIALS = {'password', 'token', 'key', 'session', 'authorization', 'cookie', 'secret'}
NOTE = '受控结构化事件v1：凭据屏蔽，身份与关联标识使用调查内代号，定位整体不可逆代号化；不接收自由文本或未知字段。代号定位须由受控来源重新核验，不能直接打开原链接。'


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Fields(StrictInput):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    document: str | None = None
    correlation_id: str | None = None
    record_id: str | None = None
    password: str | None = None
    token: str | None = None
    key: str | None = None
    session: str | None = None
    authorization: str | None = None
    cookie: str | None = None
    secret: str | None = None

    @field_validator('*')
    @classmethod
    def bounded_value(cls, value):
        if value is not None and (not value or len(value) > 4096 or
                any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
            raise ValueError('不支持的字段值')
        return value


class Query(StrictInput):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    fields: Fields


class Record(StrictInput):
    event: Literal['request_failed', 'request_completed', 'dependency_failed']
    error_code: Literal['ETIMEDOUT', 'ECONNREFUSED', 'EACCES', 'HTTP_400', 'HTTP_401', 'HTTP_403', 'HTTP_404', 'HTTP_429', 'HTTP_500', 'HTTP_502', 'HTTP_503', 'NONE']
    fields: Fields


class RecordIdentity(StrictInput):
    kind: Literal['record']
    record_id: str = Field(min_length=1, max_length=4096)

    _bounded = field_validator('record_id')(Fields.bounded_value.__func__)


class FileIdentity(StrictInput):
    kind: Literal['file']
    file_id: str = Field(min_length=1, max_length=4096)
    offset: int = Field(ge=0, le=9223372036854775807)

    _bounded = field_validator('file_id')(Fields.bounded_value.__func__)


class Material(StrictInput):
    format: Literal['controlled-event-v1']
    source_id: str = Field(min_length=1, max_length=120)
    occurred_at: AwareDatetime
    query: Query
    record: Record
    locator: str | None = Field(default=None, min_length=1, max_length=4096)
    identity: Annotated[RecordIdentity | FileIdentity, Field(discriminator='kind')] | None = None


def evidence_views(connection, attempt_id):
    rows = connection.execute(select(evidence).where(evidence.c.attempt_id == attempt_id)
                              .order_by(evidence.c.created_at, evidence.c.id)).mappings()
    return [dict(row['material'], id=str(row['id']), identity_status=row['identity_status']) for row in rows]


def evidence_router(engine, credential):
    router = APIRouter(prefix='/executor', include_in_schema=False)

    @router.post('/investigations/{investigation_id}/attempts/{attempt_id}/executions/{execution_id}/evidence', status_code=201)
    async def save(investigation_id: UUID, attempt_id: UUID, execution_id: UUID, request: Request, response: Response,
                   authorization: Annotated[str | None, Header()] = None):
        if not credential or not authorization or not secrets.compare_digest(
                authorization.encode('utf-8'), f'Bearer {credential}'.encode('utf-8')):
            raise HTTPException(401, '受控执行凭证无效')
        payload = None
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    raise ValueError()
            payload = Material.model_validate_json(bytes(body))
        except (ValidationError, ValueError):
            pass
        except Exception:
            raise HTTPException(503, '证据读取中断，未纳入分析') from None
        try:
            with engine.begin() as connection:
                parent = connection.execute(select(investigations.c.id).where(
                    investigations.c.id == investigation_id).with_for_update()).first()
                row = connection.execute(select(attempts).join(executions, executions.c.attempt_id == attempts.c.id).where(
                    attempts.c.investigation_id == investigation_id, attempts.c.id == attempt_id,
                    attempts.c.status == '调查中', executions.c.id == execution_id,
                    executions.c.valid.is_(True)).with_for_update()).mappings().first()
                if parent is None or row is None:
                    raise HTTPException(403, '执行身份或轮次无效')
                if payload is None:
                    connection.execute(update(attempts).where(attempts.c.id == attempt_id).values(evidence_unavailable=True))
                    # 提交的只有固定布尔提示；拒绝原文不进入SQL或异常记录。
                    connection.commit()
                    raise HTTPException(422, '证据不可用：仅支持受控结构化事件v1，未知字段、自由文本或非法材料已拒绝；未保存原文')
                if payload.source_id not in row['source_ids']:
                    raise HTTPException(422, '证据不可用：来源不属于本轮配置')
                if not (row['starts_at'] <= payload.query.starts_at <= payload.occurred_at < payload.query.ends_at <= row['ends_at']):
                    raise HTTPException(422, '证据不可用：发生时间或查询范围不属于本轮')
                key = connection.execute(select(redaction_keys.c.secret).where(
                    redaction_keys.c.investigation_id == investigation_id)).scalar_one_or_none()
                if key is None:
                    key = secrets.token_bytes(32)
                    connection.execute(insert(redaction_keys).values(investigation_id=investigation_id, secret=key))

                def alias(kind, value):
                    return hmac.new(key, json.dumps([kind, value], ensure_ascii=True).encode(), hashlib.sha256).hexdigest()

                def redact(fields):
                    return {field: '[已屏蔽]' if field in CREDENTIALS else '代号-' + alias(field, value)
                            for field, value in fields.model_dump(exclude_none=True).items()}

                # 来源匹配轮次快照后整体代号化；URL所有组成部分均不透传。
                result = {'source': '来源代号-' + alias('source', payload.source_id),
                    'occurred_at': payload.occurred_at.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                    'query': {'starts_at': payload.query.starts_at.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                              'ends_at': payload.query.ends_at.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                              'fields': redact(payload.query.fields)},
                    'snippet': {'event': payload.record.event, 'error_code': payload.record.error_code,
                                'fields': redact(payload.record.fields)},
                    'locator': '定位代号-' + alias('locator', payload.locator) if payload.locator else None,
                    'redaction_note': NOTE}
                identity_key = alias('evidence-identity-v1', [payload.source_id, payload.identity.model_dump()]) if payload.identity else None
                # 比较原始受控事件而非脱敏片段；查询与定位是获取上下文，不是原始事件内容。
                content_key = alias('evidence-content-v1', [result['occurred_at'], payload.record.model_dump()]) if identity_key else None
                if identity_key:
                    existing = connection.execute(select(evidence).where(
                        evidence.c.attempt_id == attempt_id, evidence.c.identity_key == identity_key)).mappings().first()
                    if existing:
                        if existing['content_key'] != content_key:
                            raise HTTPException(409, '证据身份冲突：同一可靠身份的原始事件内容发生变化；旧证据保留，本次未纳入')
                        response.status_code = 200
                        return dict(existing['material'], id=str(existing['id']), identity_status=existing['identity_status'])
                status = 'reliable' if identity_key else 'unavailable'
                identity = uuid4()
                now = datetime.now(timezone.utc)
                connection.execute(insert(evidence).values(id=identity, attempt_id=attempt_id, created_at=now, material=result,
                    identity_key=identity_key, content_key=content_key, identity_status=status))
                result['identity_status'] = status
                connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(updated_at=now))
                result['id'] = str(identity)
            return result
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, '证据保存结果未确认，请先读取本轮已保存材料；诊断不包含原文') from None

    return router
