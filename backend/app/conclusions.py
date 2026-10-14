import json
import secrets
from datetime import timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import Field, ValidationError
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, Table, Text, Uuid, insert, select, update

from app.attempts import attempts, executions
from app.evidence import StrictInput, evidence, included_evidence
from app.investigations import investigations, metadata

conclusions = Table('conclusions', metadata,
    Column('id', Uuid, primary_key=True), Column('attempt_id', Uuid, ForeignKey('attempts.id')),
    Column('execution_id', Uuid, ForeignKey('controlled_executions.id')),
    Column('version', Integer), Column('request_key', Uuid), Column('request_body', Text),
    Column('created_at', DateTime(timezone=True)), Column('material', JSON))


class Analysis(StrictInput):
    format: Literal['controlled-analysis-v1']
    kind: Literal['undetermined', 'hypothesis', 'confirmed']
    mechanism: Literal['dependency_error_propagation'] | None = None
    evidence_ids: list[UUID] = Field(max_length=32)
    missing: list[Literal['causal_trace', 'dependency_observation', 'alternative_exclusion']] = Field(max_length=3)
    expected_version: int = Field(ge=0)
    finish: bool


def conclusion_views(connection, attempt_id):
    from app.verification import conclusion_check_view
    rows = connection.execute(select(conclusions.c.material).where(conclusions.c.attempt_id == attempt_id)
                              .order_by(conclusions.c.version)).scalars()
    return [conclusion_check_view(connection, material) for material in rows]


def conclusion_router(engine, credential, clock):
    router = APIRouter(prefix='/executor', include_in_schema=False)

    @router.post('/investigations/{investigation_id}/attempts/{attempt_id}/executions/{execution_id}/conclusions', status_code=201)
    async def save(investigation_id: UUID, attempt_id: UUID, execution_id: UUID, request: Request,
                   response: Response, idempotency_key: Annotated[UUID, Header()],
                   authorization: Annotated[str | None, Header()] = None):
        if not credential or not authorization or not secrets.compare_digest(
                authorization.encode('utf-8'), f'Bearer {credential}'.encode('utf-8')):
            raise HTTPException(401, '受控执行凭证无效')
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    raise ValueError()
            payload = Analysis.model_validate_json(bytes(body))
        except (ValidationError, ValueError):
            raise HTTPException(422, '结论不可用：仅支持受控分析结构，未保存原始输入') from None
        except Exception:
            raise HTTPException(503, '结论读取中断，未保存原始输入') from None
        return save_analysis(engine, clock, investigation_id, attempt_id, execution_id,
                             payload, response, idempotency_key)

    return router


def save_agent_analysis(engine, clock, investigation_id, attempt_id, execution_id, analysis):
    from app.agent_runner import locked_attempt
    from app.verification import evidence_usable, restore_linked_conclusion
    with engine.begin() as connection:
        row = locked_attempt(connection, {'attempt_id': attempt_id, 'execution_id': execution_id}, clock)
        from app.deadlines import finalize_deadline
        pending = connection.begin_nested()
        saved = connection.execute(included_evidence(attempt_id)).mappings().all()
        usable = {str(item['id']): item for item in saved if evidence_usable(connection, item)}
        ids = [str(value) for value in analysis.evidence_ids]
        if len(set(ids)) != len(ids) or any(value not in usable for value in ids):
            raise HTTPException(422, '分析引用不属于本轮有效证据')
        from app.queries import queries
        if connection.execute(select(queries.c.id).where(queries.c.attempt_id == attempt_id, queries.c.active.is_(True))).first():
            raise HTTPException(409, '查询尚未退出')
        # A trace can prove propagation, but cannot validate arbitrary model prose.
        # Confirmation remains available through the separate controlled-analysis protocol.
        kind = 'hypothesis' if ids and analysis.kind != 'undetermined' else 'undetermined'
        history = conclusion_views(connection, attempt_id)
        now = clock()
        result = {'id': str(uuid4()), 'version': len(history) + 1, 'created_at': now.isoformat(),
            'format': 'agent-analysis-v1', 'kind': kind, 'category': analysis.category,
            'verification': 'insufficient', 'mechanism': None,
            'phenomenon': analysis.phenomenon, 'causal_chain': analysis.explanation,
            'alternatives': analysis.alternatives, 'contradictions': analysis.contradictions,
            'evidence_ids': ids, 'missing': analysis.missing_evidence,
            'recommendations': analysis.recommendations,
            'boundary': '真实模型分析；模拟日志来源。模型判断不是因果证明，建议不会自动执行。'}
        if any(((item['material'].get('snippet') or {}).get('causal_trace') or {}).get(
                'contradiction') == 'unresolved' for item in saved):
            # Structured source contradictions override a model's unsupported exclusion.
            result.update(phenomenon='本轮存在请求失败报告及未解决矛盾',
                causal_chain='受控轨迹存在未解决矛盾，尚不能据此确定错误传播路径或根因。',
                contradictions='本轮受控轨迹存在未解决矛盾；不能确认传播机制或排除其他原因。',
                missing=['需要解决受控轨迹中的关键矛盾', *analysis.missing_evidence][:8])
        connection.execute(insert(conclusions).values(id=UUID(result['id']), attempt_id=attempt_id,
            execution_id=execution_id, version=result['version'], request_key=uuid4(),
            request_body=json.dumps(analysis.model_dump(mode='json'), sort_keys=True), created_at=now, material=result))
        restore_linked_conclusion(connection, row, result, now)
        if analysis.impact is not None:
            from app.severity import severity_events, severity_history, assessed_level
            parent = connection.execute(select(investigations).where(investigations.c.id == investigation_id)).mappings().one()
            events = severity_history(connection, investigation_id)
            suggestion = {'id': str(uuid4()), 'sequence': len(events) + 1, 'kind': 'suggestion',
                'version': parent['severity_version'], 'base_version': parent['severity_version'],
                'attempt_id': str(attempt_id), 'execution_id': str(execution_id), 'source': 'agent',
                'severity': assessed_level(analysis.impact), 'assessed_at': now.isoformat(),
                'basis': analysis.impact.model_dump(), 'requires_confirmation': True,
                'boundary': '真实模型影响建议，必须人工确认；不代表根因可信度。'}
            connection.execute(insert(severity_events).values(id=UUID(suggestion['id']), investigation_id=investigation_id,
                request_key=uuid4(), request_body=json.dumps(suggestion, sort_keys=True),
                material=suggestion, sequence=suggestion['sequence']))
        connection.execute(update(attempts).where(attempts.c.id == attempt_id).values(status='已完成',
            result='未确定根因', ended_at=now, end_reason='normal_completion'))
        connection.execute(update(executions).where(executions.c.id == execution_id).values(valid=False))
        from app.agent_storage import runs
        connection.execute(update(runs).where(runs.c.execution_id == execution_id).values(
            phase='completed', reason='normal_completion', active=False, updated_at=now))
        connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(updated_at=now))
        finalize_deadline(connection, row, clock, pending)
        return result


def save_analysis(engine, clock, investigation_id, attempt_id, execution_id,
                  payload, response, idempotency_key):
    canonical = json.dumps(payload.model_dump(mode='json'), sort_keys=True)
    try:
        with engine.begin() as connection:
            parent = connection.execute(select(investigations.c.id).where(
                investigations.c.id == investigation_id).with_for_update()).first()
            row = connection.execute(select(attempts, executions.c.valid).join(executions,
                executions.c.attempt_id == attempts.c.id).where(attempts.c.investigation_id == investigation_id,
                attempts.c.id == attempt_id, executions.c.id == execution_id).with_for_update()).mappings().first()
            if parent is None or row is None:
                raise HTTPException(403, '执行身份或轮次无效')
            from app.deadlines import enforce_deadline, finalize_deadline
            enforce_deadline(connection, row, clock())
            pending = connection.begin_nested()
            if row['cancel_requested_at'] is not None or row['lost_requested_at'] is not None:
                raise HTTPException(403, '本轮已请求停止，拒绝旧执行及重放产物')
            replay = connection.execute(select(conclusions).where(conclusions.c.attempt_id == attempt_id,
                conclusions.c.request_key == idempotency_key)).mappings().first()
            # 结束请求本身可重放，但不重新授权执行或接收任何新产物。
            if replay:
                if replay['request_body'] != canonical or replay['execution_id'] != execution_id:
                    raise HTTPException(409, '结论请求标识已用于不同内容')
                finalize_deadline(connection, row, clock, pending)
                response.status_code = 200
                return replay['material']
            if row['status'] != '调查中' or not row['valid']:
                raise HTTPException(403, '执行身份或轮次无效')
            from app.queries import queries
            if payload.finish and connection.execute(select(queries.c.id).where(
                    queries.c.attempt_id == attempt_id, queries.c.active.is_(True))).first():
                raise HTTPException(409, '本轮查询尚未确认退出，不能结束调查')
            history = conclusion_views(connection, attempt_id)
            if payload.expected_version != len(history):
                raise HTTPException(409, '结论版本已变化，请读取当前版本')
            saved = connection.execute(included_evidence(attempt_id).where(evidence.c.id.in_(payload.evidence_ids))).mappings().all()
            if len(saved) != len(set(payload.evidence_ids)) or len(set(payload.evidence_ids)) != len(payload.evidence_ids):
                raise HTTPException(422, '证据必须为本轮已纳入的不同证据')
            if payload.kind == 'hypothesis':
                hypothesis_supported = False
                for item in saved:
                    record = item['material'].get('snippet') or {}
                    event, error = record.get('event'), record.get('error_code')
                    # 依赖失败本身或兼容传播的请求错误只提供候选依据，不证明传播链。
                    hypothesis_supported = hypothesis_supported or (
                        event == 'dependency_failed' and error in {
                            'ETIMEDOUT', 'ECONNREFUSED', 'EACCES', 'HTTP_400', 'HTTP_401',
                            'HTTP_403', 'HTTP_404', 'HTTP_429', 'HTTP_500', 'HTTP_502', 'HTTP_503'}
                        or event == 'request_failed' and error in {
                            'ETIMEDOUT', 'ECONNREFUSED', 'EACCES', 'HTTP_500', 'HTTP_502', 'HTTP_503'})
                if payload.mechanism != 'dependency_error_propagation' or not hypothesis_supported:
                    raise HTTPException(422, '待验证假设须有支持候选机制的本轮事件依据与缺失证据')
            if payload.kind == 'undetermined' and payload.mechanism is not None:
                raise HTTPException(422, '未确定根因不声明已知机制')
            if payload.kind != 'confirmed' and not payload.missing:
                raise HTTPException(422, '未确认结论须明确缺失证据')
            supported = False
            unresolved = False
            from app.verification import evidence_usable, trace_supports, restore_linked_conclusion
            all_saved = connection.execute(included_evidence(attempt_id)).mappings().all()
            for item in all_saved:
                record = item['material'].get('snippet') or {}
                trace = record.get('causal_trace')
                if trace:
                    matches = trace_supports(item['material'])
                    supported = supported or (matches and evidence_usable(connection, item) and item['id'] in payload.evidence_ids)
                    unresolved = unresolved or not matches
            if payload.kind == 'confirmed' and (payload.mechanism != 'dependency_error_propagation'
                    or payload.missing or not supported or unresolved or row['evidence_unavailable']):
                raise HTTPException(422, '现有材料不足以支持受控因果机制，或仍有关键矛盾与替代解释')
            now = clock()
            result = {'id': str(uuid4()), 'version': len(history) + 1,
                'created_at': now.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                'kind': payload.kind, 'verification': 'insufficient', 'mechanism': payload.mechanism,
                'evidence_ids': [str(identity) for identity in payload.evidence_ids], 'missing': payload.missing,
                'boundary': '受控分析v1；证据不足不表示没有故障；未接真实Agent。'}
            if payload.kind == 'confirmed':
                result.update(verification='supported_controlled_trace', phenomenon='请求失败',
                    causal_chain='依赖调用失败，经同一次调用的错误传播路径导致请求失败。',
                    alternatives='受控轨迹观测排除本地拒绝与独立请求失败；不排除未建模的其他原因。',
                    contradictions='引用轨迹未报告未处理关键矛盾；不等于全局不存在矛盾。',
                    boundary='仅支持可信受控生产者声明的同调用错误传播轨迹；非时间相关、非科学证明、非真实Agent验收。')
            connection.execute(insert(conclusions).values(id=UUID(result['id']), attempt_id=attempt_id,
                execution_id=execution_id, version=result['version'], request_key=idempotency_key,
                request_body=canonical, created_at=now, material=result))
            restore_linked_conclusion(connection, row, result, now)
            values = {'updated_at': now}
            if payload.finish:
                connection.execute(update(attempts).where(attempts.c.id == attempt_id).values(
                    status='已完成', result='已确认根因' if payload.kind == 'confirmed' else '未确定根因', ended_at=now, end_reason='normal_completion'))
                connection.execute(update(executions).where(executions.c.id == execution_id).values(valid=False))
                values['retention_expires_at'] = None
            connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(**values))
            finalize_deadline(connection, row, clock, pending)
        return result
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, '结论保存结果未确认，请使用同一请求标识重试；诊断不包含原文') from None
