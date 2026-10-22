import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
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

attempt_evidence = Table('attempt_evidence', metadata,
    Column('attempt_id', Uuid, ForeignKey('attempts.id'), primary_key=True),
    Column('evidence_id', Uuid, ForeignKey('evidence.id'), primary_key=True),
    Column('included_at', DateTime(timezone=True), nullable=False))

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


class CausalTrace(StrictInput):
    # 可信受控生产者声明的同调用错误传播观测，不从时间/数量推断。
    format: Literal['observed-propagation-v1']
    dependency_error: Literal['ETIMEDOUT', 'ECONNREFUSED', 'EACCES', 'HTTP_500', 'HTTP_502', 'HTTP_503']
    propagated_error: Literal['ETIMEDOUT', 'ECONNREFUSED', 'EACCES', 'HTTP_500', 'HTTP_502', 'HTTP_503']
    request_outcome: Literal['failed_by_dependency', 'independent_failure', 'unknown']
    local_rejection: Literal['excluded_by_trace', 'unresolved']
    independent_failure: Literal['excluded_by_trace', 'unresolved']
    contradiction: Literal['none_observed', 'unresolved']


class Observation(StrictInput):
    component: Literal['gateway', 'application', 'database', 'redis']
    signal: Literal['http_response', 'request_timeout', 'dependency_timeout', 'pool_capacity',
                    'configuration_missing', 'permission_denied']
    http_status: int | None = Field(default=None, ge=100, le=599)
    used: int | None = Field(default=None, ge=0, le=1_000_000)
    capacity: int | None = Field(default=None, ge=1, le=1_000_000)
    unit: Literal['connections'] | None = None

    @model_validator(mode='after')
    def coherent(self):
        if self.signal == 'http_response':
            if self.component != 'gateway' or self.http_status is None or any(
                    value is not None for value in (self.used, self.capacity, self.unit)):
                raise ValueError('Invalid HTTP observation')
        elif self.signal == 'pool_capacity':
            if (self.component != 'database' or self.used is None or self.capacity is None
                    or self.used > self.capacity or self.unit != 'connections' or self.http_status is not None):
                raise ValueError('Invalid pool observation')
        elif any(value is not None for value in (self.http_status, self.used, self.capacity, self.unit)):
            raise ValueError('Unexpected observation measurements')
        if self.signal == 'request_timeout' and self.component != 'application':
            raise ValueError('Invalid request component')
        if self.signal == 'dependency_timeout' and self.component not in ('redis', 'database'):
            raise ValueError('Invalid dependency component')
        return self


class Record(StrictInput):
    event: Literal['request_failed', 'request_completed', 'dependency_failed']
    error_code: Literal['ETIMEDOUT', 'ECONNREFUSED', 'EACCES', 'HTTP_400', 'HTTP_401', 'HTTP_403', 'HTTP_404', 'HTTP_429', 'HTTP_500', 'HTTP_502', 'HTTP_503', 'NONE']
    fields: Fields
    causal_trace: CausalTrace | None = None


class RecordV2(Record):
    observation: Observation

    @model_validator(mode='after')
    def observation_matches(self):
        expected = {'request_timeout': ('request_failed', 'ETIMEDOUT'),
                    'dependency_timeout': ('dependency_failed', 'ETIMEDOUT'),
                    'pool_capacity': ('dependency_failed', 'NONE'),
                    'configuration_missing': ('request_failed', 'NONE'),
                    'permission_denied': ('request_failed', 'EACCES')}
        if self.observation.signal == 'http_response':
            if self.error_code != 'NONE' or self.event != ('request_failed' if self.observation.http_status >= 400 else 'request_completed'):
                raise ValueError('HTTP event mismatch')
        elif (self.event, self.error_code) != expected[self.observation.signal]:
            raise ValueError('Observation event mismatch')
        if self.causal_trace and self.observation.signal != 'request_timeout':
            raise ValueError('Trace requires application request timeout')
        return self


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
    format: Literal['controlled-event-v1', 'controlled-location-v1', 'controlled-event-v2']
    source_id: str = Field(min_length=1, max_length=120)
    occurred_at: AwareDatetime
    query: Query
    record: Record | RecordV2 | None
    locator: str | None = Field(default=None, min_length=1, max_length=4096)
    identity: Annotated[RecordIdentity | FileIdentity, Field(discriminator='kind')] | None = None

    @model_validator(mode='after')
    def material_kind(self):
        if self.format in ('controlled-event-v1', 'controlled-event-v2') and self.record is None:
            raise ValueError('event requires retained material')
        if (self.format == 'controlled-event-v2') != isinstance(self.record, RecordV2):
            raise ValueError('Record version mismatch')
        if self.format == 'controlled-location-v1' and (self.record is not None or not self.locator or self.identity):
            raise ValueError('location-only requires locator, no record identity or event')
        return self


def included_evidence(attempt_id):
    return select(evidence).join(attempt_evidence, attempt_evidence.c.evidence_id == evidence.c.id).where(
        attempt_evidence.c.attempt_id == attempt_id)


def evidence_view(connection, row):
    from app.verification import evidence_check_view
    origin = connection.execute(select(attempts.c.attempt_number).where(attempts.c.id == row['attempt_id'])).scalar_one()
    return dict(row['material'], id=str(row['id']), identity_status=row['identity_status'],
        source_attempt_id=str(row['attempt_id']), source_attempt_number=origin,
        **evidence_check_view(connection, row))


def verifiable(material):
    try:
        snippet = material['snippet']
        (RecordV2 if 'observation' in snippet else Record).model_validate(snippet)
        Query.model_validate_json(json.dumps(material['query']))
        return bool(material['source']) and datetime.fromisoformat(material['occurred_at']).tzinfo is not None
    except (KeyError, TypeError, ValueError):
        return False


def historical_reference_states(connection, target):
    from app.verification import evidence_usable
    historical = connection.execute(select(evidence).join(attempts, attempts.c.id == evidence.c.attempt_id).where(
        attempts.c.investigation_id == target['investigation_id'],
        attempts.c.attempt_number < target['attempt_number'])).mappings().all()
    present = connection.execute(included_evidence(target['id'])).mappings().all()
    from app.queries import coverage_aliases
    sources = coverage_aliases(connection, target['id'], target['source_ids'])
    states = {}
    for source in historical:
        equivalent = next((item for item in present if item['id'] == source['id'] or (
            source['identity_key'] and item['identity_key'] == source['identity_key']
            and source['content_key'] and item['content_key'] == source['content_key'])), None)
        origin = connection.execute(select(attempts).where(attempts.c.id == source['attempt_id'])).mappings().one()
        material = source['material']
        allowed = (evidence_usable(connection, source) and origin['service_id'] == target['service_id']
            and material['source'] in sources
            and target['starts_at'] <= datetime.fromisoformat(material['query']['starts_at']) <=
            datetime.fromisoformat(material['occurred_at']) < datetime.fromisoformat(material['query']['ends_at']) <= target['ends_at'])
        conflict = source['identity_key'] and any(item['identity_key'] == source['identity_key']
            and item['content_key'] != source['content_key'] for item in [*historical, *present])
        states[str(source['id'])] = {'included_evidence_id': str(equivalent['id']) if equivalent else None,
            'reference_allowed': bool(allowed and not conflict)}
    return states


def evidence_views(connection, attempt_id):
    rows = connection.execute(included_evidence(attempt_id).order_by(evidence.c.created_at, evidence.c.id)).mappings()
    return [evidence_view(connection, row) for row in rows]


class ReferenceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    evidence_id: UUID


def reference_router(engine, clock, credential):
    router = APIRouter()

    @router.post('/executor/investigations/{investigation_id}/attempts/{attempt_id}/executions/{execution_id}/evidence-references', status_code=201, include_in_schema=False)
    @router.post('/investigations/{investigation_id}/attempts/{attempt_id}/evidence-references', status_code=201)
    def reference(investigation_id: UUID, attempt_id: UUID, payload: ReferenceInput, response: Response,
                  request: Request, execution_id: UUID | None = None,
                  authorization: Annotated[str | None, Header()] = None):
        controlled = '/executions/{execution_id}/' in request.scope['route'].path
        if controlled and (not credential or not authorization or not secrets.compare_digest(
                authorization.encode('utf-8'), f'Bearer {credential}'.encode('utf-8'))):
            raise HTTPException(401, '受控执行凭证无效')
        try:
            with engine.begin() as connection:
                parent = connection.execute(select(investigations.c.id).where(
                    investigations.c.id == investigation_id).with_for_update()).first()
                row = connection.execute(select(attempts).join(executions, executions.c.attempt_id == attempts.c.id).where(
                    attempts.c.investigation_id == investigation_id, attempts.c.id == attempt_id,
                    attempts.c.status == '调查中', executions.c.valid.is_(True),
                    *((executions.c.id == execution_id,) if controlled else ())).with_for_update()).mappings().first()
                if parent is None or row is None:
                    raise HTTPException(403, '执行身份或轮次无效')
                from app.deadlines import enforce_deadline, finalize_deadline
                enforce_deadline(connection, row, clock())
                pending = connection.begin_nested()
                source = connection.execute(select(evidence).join(attempts, attempts.c.id == evidence.c.attempt_id).where(
                    evidence.c.id == payload.evidence_id, attempts.c.investigation_id == investigation_id,
                    attempts.c.attempt_number < row['attempt_number'])).mappings().first()
                if source is None:
                    raise HTTPException(422, '历史证据不属于本调查先前轮次')
                origin = connection.execute(select(attempts).where(attempts.c.id == source['attempt_id'])).mappings().one()
                material = source['material']
                from app.verification import evidence_usable
                if not evidence_usable(connection, source):
                    raise HTTPException(422, '历史证据不可核验：缺少保留的受控片段、来源、时间或查询条件')
                from app.queries import coverage_aliases
                if origin['service_id'] != row['service_id'] or material['source'] not in coverage_aliases(connection, attempt_id, row['source_ids']):
                    raise HTTPException(422, '历史证据服务或来源不符合本轮范围')
                if not (row['starts_at'] <= datetime.fromisoformat(material['query']['starts_at']) <=
                        datetime.fromisoformat(material['occurred_at']) < datetime.fromisoformat(material['query']['ends_at']) <= row['ends_at']):
                    raise HTTPException(422, '历史证据发生时间或查询范围不符合本轮固定范围')
                if source['identity_key']:
                    matches = connection.execute(select(evidence).join(attempts, attempts.c.id == evidence.c.attempt_id).where(
                        attempts.c.investigation_id == investigation_id,
                        evidence.c.identity_key == source['identity_key'])).mappings().all()
                    if any(item['content_key'] != source['content_key'] for item in matches):
                        raise HTTPException(409, '证据身份冲突：同一可靠身份的原始事件内容发生变化；旧证据保留，本次未纳入')
                present = connection.execute(included_evidence(attempt_id).where(
                    evidence.c.identity_key == source['identity_key'] if source['identity_key'] else
                    evidence.c.id == source['id'])).mappings().first()
                if present:
                    source = present
                    response.status_code = 200
                else:
                    now = clock()
                    connection.execute(insert(attempt_evidence).values(attempt_id=attempt_id, evidence_id=source['id'], included_at=now))
                    connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(updated_at=now))
                result = evidence_view(connection, source)
                finalize_deadline(connection, row, clock, pending)
            return result
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, '历史证据纳入结果未确认，请读取本轮材料；诊断不包含原文') from None

    return router


def evidence_router(engine, credential, clock):
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
        return save_material(engine, clock, investigation_id, attempt_id, execution_id, payload, response)

    return router


def save_material(engine, clock, investigation_id, attempt_id, execution_id, payload, response, *, source_mode=None):
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
            from app.deadlines import enforce_deadline, finalize_deadline
            enforce_deadline(connection, row, clock())
            pending = connection.begin_nested()
            if payload is None:
                connection.execute(update(attempts).where(attempts.c.id == attempt_id).values(evidence_unavailable=True))
                # 提交的只有固定布尔提示；拒绝原文不进入SQL或异常记录。
                finalize_deadline(connection, row, clock, pending)
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
                            'fields': redact(payload.record.fields)} if payload.record else None,
                'locator': '定位代号-' + alias('locator', payload.locator) if payload.locator else None,
                'redaction_note': NOTE}
            if source_mode == 'simulated':
                result['source_mode'] = 'simulated'
            if isinstance(payload.record, RecordV2):
                result['snippet']['observation'] = payload.record.observation.model_dump(exclude_none=True)
                result['format'] = 'controlled-event-v2'
            if payload.record and payload.record.causal_trace is not None:
                result['snippet']['causal_trace'] = payload.record.causal_trace.model_dump()
            identity_key = alias('evidence-identity-v1', [payload.source_id, payload.identity.model_dump()]) if payload.identity else None
            # 比较原始受控事件而非脱敏片段；查询与定位是获取上下文，不是原始事件内容。
            content_key = alias('evidence-content-v1', [result['occurred_at'], payload.record.model_dump(exclude={'causal_trace'} if payload.record.causal_trace is None else set())]) if identity_key else None
            if identity_key:
                matches = connection.execute(select(evidence).join(attempts, attempts.c.id == evidence.c.attempt_id).where(
                    attempts.c.investigation_id == investigation_id, evidence.c.identity_key == identity_key)
                    .order_by(attempts.c.attempt_number, evidence.c.created_at, evidence.c.id)).mappings().all()
                if any(item['content_key'] != content_key for item in matches):
                    raise HTTPException(409, '证据身份冲突：同一可靠身份的原始事件内容发生变化；旧证据保留，本次未纳入')
                if any(item['material'].get('source_mode') != result.get('source_mode') for item in matches):
                    raise HTTPException(409, '证据来源模式冲突：模拟与其他材料不能共享可靠身份；旧证据保留')
                if matches:
                    existing = matches[0]
                    present = connection.execute(included_evidence(attempt_id).where(evidence.c.identity_key == identity_key)).mappings().first()
                    if present:
                        existing = present
                        response.status_code = 200
                    else:
                        # 新获取已通过本轮范围校验；旧材料不适用时保存本次核验上下文，不改历史。
                        existing = None
                        for candidate in matches:
                            origin = connection.execute(select(attempts).where(attempts.c.id == candidate['attempt_id'])).mappings().one()
                            retained = candidate['material']
                            from app.verification import evidence_usable
                            if (evidence_usable(connection, candidate) and origin['service_id'] == row['service_id']
                                    and retained['source'] == result['source']
                                    and row['starts_at'] <= datetime.fromisoformat(retained['query']['starts_at']) <=
                                    datetime.fromisoformat(retained['occurred_at']) < datetime.fromisoformat(retained['query']['ends_at']) <= row['ends_at']):
                                existing = candidate
                                break
                        if existing:
                            now = clock()
                            connection.execute(insert(attempt_evidence).values(attempt_id=attempt_id, evidence_id=existing['id'], included_at=now))
                            connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(updated_at=now))
                    if existing:
                        result = evidence_view(connection, existing)
                        finalize_deadline(connection, row, clock, pending)
                        return result
            status = 'reliable' if identity_key else 'unavailable'
            identity = uuid4()
            now = clock()
            connection.execute(insert(evidence).values(id=identity, attempt_id=attempt_id, created_at=now, material=result,
                identity_key=identity_key, content_key=content_key, identity_status=status))
            connection.execute(insert(attempt_evidence).values(attempt_id=attempt_id, evidence_id=identity, included_at=now))
            result['identity_status'] = status
            result['source_attempt_id'] = str(attempt_id)
            result['source_attempt_number'] = row['attempt_number']
            result['verification'] = 'retained_structured_material' if verifiable(result) else 'unverifiable'
            result.update(source_access='unknown', verification_version=0, verification_history=[])
            connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(updated_at=now))
            result['id'] = str(identity)
            finalize_deadline(connection, row, clock, pending)
        return result
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, '证据保存结果未确认，请先读取本轮已保存材料；诊断不包含原文') from None
