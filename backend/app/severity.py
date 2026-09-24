import json
import secrets
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import Field, ValidationError
from sqlalchemy import Column, ForeignKey, Integer, JSON, Table, Text, Uuid, insert, select, update

from app.evidence import StrictInput
from app.investigations import investigations, metadata

severity_events = Table('severity_events', metadata,
    Column('id', Uuid, primary_key=True),
    Column('investigation_id', Uuid, ForeignKey('investigations.id')),
    Column('request_key', Uuid), Column('request_body', Text),
    Column('material', JSON), Column('sequence', Integer))


class Impact(StrictInput):
    format: Literal['controlled-impact-v1']
    business: Literal['core_unavailable', 'key_operations_blocked', 'partial_or_slow', 'local_minor', 'insufficient']
    users: Literal['broad', 'partial', 'local', 'unknown']
    duration: Literal['ongoing', 'intermittent', 'ended', 'unknown']
    alternative: Literal['available', 'none', 'unknown', 'not_needed']


class Confirmation(StrictInput):
    expected_version: int = Field(ge=0)


class HumanAssessment(Confirmation):
    basis: Impact


def assessed_level(basis):
    if basis.business == 'core_unavailable':
        return '高'
    if basis.business == 'key_operations_blocked' and basis.users == 'broad':
        return '高'
    if basis.business == 'insufficient':
        return '未评估'
    if basis.alternative == 'available' and (
            basis.business == 'partial_or_slow'
            or (basis.business == 'key_operations_blocked' and basis.users in ('partial', 'local'))):
        return '中'
    if basis.business == 'local_minor' and basis.users == 'local' and basis.alternative in ('available', 'not_needed'):
        return '低'
    return '未评估'


def severity_history(connection, investigation_id):
    return list(connection.execute(select(severity_events.c.material).where(
        severity_events.c.investigation_id == investigation_id).order_by(severity_events.c.sequence)).scalars())


def severity_view(connection, investigation_id, severity_version, latest_attempt):
    history = severity_history(connection, investigation_id)
    latest_id = str(latest_attempt['id']) if latest_attempt else None
    running = latest_attempt is not None and latest_attempt['status'] == '调查中'
    applied = {item.get('suggestion_id') for item in history if item['kind'] == 'confirmation'}
    suggestions = [dict(item, status='applied' if item['id'] in applied else 'pending'
        if item['base_version'] == severity_version and item['attempt_id'] == latest_id and running else 'stale')
        for item in history if item['kind'] == 'suggestion']
    return history, suggestions


async def read_input(request, model):
    try:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                raise ValueError()
        return model.model_validate_json(bytes(body))
    except (ValidationError, ValueError):
        raise HTTPException(422, '评估依据不可用：仅接受受控定性业务影响结构，不接收自由文本、未知字段或敏感原文') from None
    except Exception:
        raise HTTPException(503, '评估读取中断，未保存原始输入') from None


def severity_router(engine, credential, clock):
    router = APIRouter()

    async def adjust(investigation_id, request, response, idempotency_key, authorization,
                     attempt_id=None, execution_id=None, suggestion_id=None):
        controlled = execution_id is not None
        if controlled and (not credential or not authorization or not secrets.compare_digest(
                authorization.encode('utf-8'), f'Bearer {credential}'.encode('utf-8'))):
            raise HTTPException(401, '受控执行凭证无效')
        payload = await read_input(request, Confirmation if suggestion_id else HumanAssessment)
        canonical = json.dumps({'operation': 'agent' if controlled else 'confirm' if suggestion_id else 'human',
            'attempt_id': str(attempt_id) if attempt_id else None,
            'execution_id': str(execution_id) if execution_id else None,
            'suggestion_id': str(suggestion_id) if suggestion_id else None,
            'payload': payload.model_dump(mode='json')}, sort_keys=True)
        try:
            with engine.begin() as connection:
                row = connection.execute(select(investigations).where(
                    investigations.c.id == investigation_id).with_for_update()).mappings().first()
                if row is None:
                    raise HTTPException(404, '调查不存在')
                savepoint = execution = None
                if controlled:
                    from app.attempts import attempts, executions
                    from app.deadlines import enforce_deadline, finalize_deadline
                    execution = connection.execute(select(attempts).join(executions, executions.c.attempt_id == attempts.c.id).where(
                        attempts.c.investigation_id == investigation_id, attempts.c.id == attempt_id,
                        attempts.c.status == '调查中', executions.c.id == execution_id,
                        executions.c.valid.is_(True)).with_for_update()).mappings().first()
                    if execution is None:
                        raise HTTPException(403, '执行身份或轮次无效')
                    enforce_deadline(connection, execution, clock())
                    savepoint = connection.begin_nested()
                    if execution['cancel_requested_at'] or execution['lost_requested_at']:
                        raise HTTPException(403, '本轮已请求停止，拒绝旧执行及重放产物')
                replay = connection.execute(select(severity_events).where(
                    severity_events.c.investigation_id == investigation_id,
                    severity_events.c.request_key == idempotency_key)).mappings().first()
                if replay:
                    if replay['request_body'] != canonical:
                        raise HTTPException(409, '等级请求标识已用于不同内容')
                    if controlled:
                        finalize_deadline(connection, execution, clock, savepoint)
                    response.status_code = 200
                    return replay['material']
                if row['severity_version'] != payload.expected_version:
                    raise HTTPException(409, '严重程度决定已变化，请刷新后重新评估')
                history = severity_history(connection, investigation_id)
                from app.attempts import attempts
                latest_row = connection.execute(select(attempts.c.id, attempts.c.status).where(
                    attempts.c.investigation_id == investigation_id)
                    .order_by(attempts.c.attempt_number.desc()).limit(1)).mappings().first()
                latest = latest_row['id'] if latest_row else None
                latest_running = latest_row is not None and latest_row['status'] == '调查中'
                decisions = [item for item in history if item['kind'] != 'suggestion']
                human_precedence = (decisions[-1]['source'] == 'human' if decisions
                                    else row['severity_source'] == 'human')
                basis = None
                if suggestion_id:
                    suggestion = next((item for item in history if item['id'] == str(suggestion_id)
                                       and item['kind'] == 'suggestion'), None)
                    if suggestion is None:
                        raise HTTPException(404, '建议不存在')
                    if (suggestion['base_version'] != row['severity_version'] or suggestion['attempt_id'] != str(latest)
                            or not latest_running
                            or any(item.get('suggestion_id') == str(suggestion_id) for item in history)):
                        raise HTTPException(409, '建议已过时或已应用，请刷新后重新评估')
                    basis = Impact.model_validate(suggestion['basis'])
                else:
                    basis = payload.basis
                now = clock()
                level = assessed_level(basis)
                is_suggestion = controlled and human_precedence
                source = 'agent' if controlled else 'human'
                result = {'id': str(uuid4()), 'sequence': len(history) + 1,
                    'kind': 'suggestion' if is_suggestion else 'agent' if controlled else 'confirmation' if suggestion_id else 'human',
                    'version': row['severity_version'] + (0 if is_suggestion else 1),
                    'base_version': row['severity_version'],
                    'attempt_id': str(execution['id']) if controlled else str(latest) if latest else None,
                    'execution_id': str(execution_id) if controlled else None,
                    'suggestion_id': str(suggestion_id) if suggestion_id else None,
                    'severity': level, 'source': source,
                    'assessed_at': now.isoformat(), 'basis': basis.model_dump(),
                    'boundary': '仅评估定性业务影响；未评估不等于低风险，与根因可信度、核验状态和优先级无关。'}
                connection.execute(insert(severity_events).values(id=UUID(result['id']), investigation_id=investigation_id,
                    request_key=idempotency_key, request_body=canonical, material=result, sequence=result['sequence']))
                if not is_suggestion:
                    values = dict(severity=level, severity_version=result['version'],
                                  severity_source=None if level == '未评估' else source,
                                  severity_assessed_at=None if level == '未评估' else now)
                    # 最后更新时间只随实际等级变化刷新；确认/重放同一等级不刷新。
                    if row['severity'] != level:
                        values['updated_at'] = now
                    connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(**values))
                if controlled:
                    finalize_deadline(connection, execution, clock, savepoint)
            return result
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, '等级保存结果未确认，请使用同一请求标识重试；诊断不包含原文') from None

    @router.post('/investigations/{investigation_id}/severity', status_code=201)
    async def human_assessment(investigation_id: UUID, request: Request, response: Response,
                               idempotency_key: Annotated[UUID, Header()],
                               authorization: Annotated[str | None, Header()] = None):
        return await adjust(investigation_id, request, response, idempotency_key, authorization)

    @router.post('/investigations/{investigation_id}/severity/suggestions/{suggestion_id}/confirm', status_code=201)
    async def confirm_suggestion(investigation_id: UUID, suggestion_id: UUID, request: Request, response: Response,
                                 idempotency_key: Annotated[UUID, Header()],
                                 authorization: Annotated[str | None, Header()] = None):
        return await adjust(investigation_id, request, response, idempotency_key, authorization,
                            suggestion_id=suggestion_id)

    @router.post('/executor/investigations/{investigation_id}/attempts/{attempt_id}/executions/{execution_id}/severity',
                 status_code=201, include_in_schema=False)
    async def executor_assessment(investigation_id: UUID, attempt_id: UUID, execution_id: UUID,
                                  request: Request, response: Response,
                                  idempotency_key: Annotated[UUID, Header()],
                                  authorization: Annotated[str | None, Header()] = None):
        return await adjust(investigation_id, request, response, idempotency_key, authorization,
                            attempt_id=attempt_id, execution_id=execution_id)

    return router
