"""Read-only-source observations, separate from execution products and round outcomes."""
import json
import secrets
from datetime import timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import Field, ValidationError, model_validator
from sqlalchemy import Column, ForeignKey, Integer, JSON, Table, Text, Uuid, insert, select, update

from app.evidence import StrictInput, evidence, included_evidence, verifiable
from app.investigations import investigations, metadata

evidence_checks = Table('evidence_checks', metadata,
    Column('id', Uuid, primary_key=True), Column('evidence_id', Uuid, ForeignKey('evidence.id')),
    Column('request_key', Uuid), Column('request_body', Text), Column('version', Integer), Column('material', JSON))
conclusion_checks = Table('conclusion_checks', metadata,
    Column('id', Uuid, primary_key=True), Column('conclusion_id', Uuid, ForeignKey('conclusions.id')),
    Column('version', Integer), Column('material', JSON))


class EvidenceCheck(StrictInput):
    expected_version: int = Field(ge=0)
    source_access: Literal['reachable', 'unreachable', 'unknown']
    material_status: Literal['retained', 'invalid', 'missing']
    reason: Literal['source_unreachable', 'source_reachable', 'material_integrity_failed', 'no_retained_material']

    @model_validator(mode='after')
    def consistent(self):
        if ((self.material_status == 'invalid') != (self.reason == 'material_integrity_failed')
                or (self.material_status == 'missing') != (self.reason == 'no_retained_material')
                or self.reason == 'source_unreachable' and self.source_access != 'unreachable'
                or self.reason == 'source_reachable' and self.source_access != 'reachable'):
            raise ValueError('inconsistent observation')
        return self


def evidence_history(connection, evidence_id):
    return list(connection.execute(select(evidence_checks.c.material).where(
        evidence_checks.c.evidence_id == evidence_id).order_by(evidence_checks.c.version)).scalars())


def evidence_usable(connection, row):
    history = evidence_history(connection, row['id'])
    return verifiable(row['material']) and (not history or history[-1]['material_status'] == 'retained')


def evidence_check_view(connection, row):
    history = evidence_history(connection, row['id'])
    return {'source_access': history[-1]['source_access'] if history else 'unknown',
            'verification': 'retained_structured_material' if evidence_usable(connection, row) else 'unverifiable',
            'verification_version': len(history), 'verification_history': history}


def conclusion_history(connection, conclusion_id):
    return list(connection.execute(select(conclusion_checks.c.material).where(
        conclusion_checks.c.conclusion_id == conclusion_id).order_by(conclusion_checks.c.version)).scalars())


def conclusion_check_view(connection, material):
    history = conclusion_history(connection, UUID(material['id']))
    if not history:
        return material
    return {**material, 'original_verification': material['verification'],
            'verification': history[-1]['status'], 'verification_history': history,
            'current_evidence_ids': history[-1]['evidence_ids'] if history[-1]['status'] != 'pending_reverification' else []}


def trace_supports(material):
    record = material.get('snippet') or {}
    trace = record.get('causal_trace') or {}
    return (record.get('event') == 'request_failed' and trace.get('format') == 'observed-propagation-v1'
        and trace.get('dependency_error') == trace.get('propagated_error') == record.get('error_code')
        and trace.get('request_outcome') == 'failed_by_dependency'
        and trace.get('local_rejection') == trace.get('independent_failure') == 'excluded_by_trace'
        and trace.get('contradiction') == 'none_observed')


def append_conclusion_check(connection, conclusion_id, status, reason, ids, now, attempt_id):
    history = conclusion_history(connection, conclusion_id)
    material = {'id': str(uuid4()), 'version': len(history) + 1, 'status': status, 'reason': reason,
        'created_at': now.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'evidence_ids': [str(identity) for identity in ids], 'attempt_id': str(attempt_id)}
    connection.execute(insert(conclusion_checks).values(id=UUID(material['id']), conclusion_id=conclusion_id,
        version=material['version'], material=material))


def reassess_conclusions(connection, investigation_id, now):
    from app.attempts import attempts
    from app.conclusions import conclusions
    rows = connection.execute(select(conclusions).join(attempts, attempts.c.id == conclusions.c.attempt_id)
        .where(attempts.c.investigation_id == investigation_id)).mappings().all()
    for row in rows:
        agent_analysis = row['material'].get('format') == 'agent-analysis-v1'
        if row['material']['kind'] != 'confirmed' and not agent_analysis:
            continue
        history = conclusion_history(connection, row['id'])
        # Loss is sticky: only a linked manual new round can restore confirmation.
        if history and history[-1]['status'] == 'pending_reverification':
            continue
        basis_attempt = UUID(history[-1]['attempt_id']) if history else row['attempt_id']
        saved = connection.execute(included_evidence(basis_attempt)).mappings().all()
        if agent_analysis and not history:
            cited = row['material']['evidence_ids']
            usable = {str(item['id']) for item in saved if evidence_usable(connection, item)}
            if any(identity not in usable for identity in cited):
                append_conclusion_check(connection, row['id'], 'pending_reverification',
                    'cited_material_unavailable', cited, now, basis_attempt)
            continue
        sufficient = [item['id'] for item in saved if evidence_usable(connection, item) and trace_supports(item['material'])]
        unresolved = any((item['material'].get('snippet') or {}).get('causal_trace')
                         and not trace_supports(item['material']) for item in saved)
        if not sufficient or unresolved:
            affected = history[-1]['evidence_ids'] if history else row['material']['evidence_ids']
            append_conclusion_check(connection, row['id'], 'pending_reverification',
                'insufficient_verifiable_causal_support', affected, now, basis_attempt)
        else:
            current_ids = history[-1]['evidence_ids'] if history else row['material']['evidence_ids']
            if not set(current_ids).intersection(str(identity) for identity in sufficient):
                append_conclusion_check(connection, row['id'], 'supported_controlled_trace',
                    'alternative_sufficient_causal_support', sufficient, now, basis_attempt)


def restore_linked_conclusion(connection, attempt, result, now):
    target = attempt['reverify_conclusion_id']
    if target and result['kind'] == 'confirmed':
        history = conclusion_history(connection, target)
        if history and history[-1]['status'] == 'pending_reverification':
            append_conclusion_check(connection, target, 'supported_controlled_trace',
                'manual_round_sufficient_causal_support', result['evidence_ids'], now, attempt['id'])


def verification_router(engine, credential, clock):
    router = APIRouter(prefix='/executor', include_in_schema=False)

    @router.post('/investigations/{investigation_id}/evidence/{evidence_id}/verification', status_code=201)
    async def observe(investigation_id: UUID, evidence_id: UUID, request: Request, response: Response,
                      idempotency_key: Annotated[UUID, Header()],
                      authorization: Annotated[str | None, Header()] = None):
        # A trusted observer can check ended rounds; this never reauthorizes their execution.
        if not credential or not authorization or not secrets.compare_digest(
                authorization.encode(), f'Bearer {credential}'.encode()):
            raise HTTPException(401, '受控核验凭证无效')
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 4096:
                    raise ValueError()
            payload = EvidenceCheck.model_validate_json(bytes(body))
        except (ValidationError, ValueError):
            raise HTTPException(422, '核验输入无效：仅接受受控状态与固定原因，不保存原文') from None
        except Exception:
            raise HTTPException(503, '核验输入读取中断') from None
        canonical = json.dumps(payload.model_dump(mode='json'), sort_keys=True)
        try:
            with engine.begin() as connection:
                from app.attempts import attempts
                parent = connection.execute(select(investigations.c.id).where(
                    investigations.c.id == investigation_id).with_for_update()).first()
                row = connection.execute(select(evidence).join(attempts, attempts.c.id == evidence.c.attempt_id).where(
                    evidence.c.id == evidence_id, attempts.c.investigation_id == investigation_id)).mappings().first()
                if parent is None or row is None:
                    raise HTTPException(404, '调查或证据不存在')
                replay = connection.execute(select(evidence_checks).where(evidence_checks.c.evidence_id == evidence_id,
                    evidence_checks.c.request_key == idempotency_key)).mappings().first()
                if replay:
                    if replay['request_body'] != canonical:
                        raise HTTPException(409, '核验请求标识已用于不同内容')
                    response.status_code = 200
                    return replay['material']
                history = evidence_history(connection, evidence_id)
                if len(history) != payload.expected_version:
                    raise HTTPException(409, '核验版本已变化，请读取最新状态')
                if payload.material_status == 'retained' and not verifiable(row['material']):
                    raise HTTPException(422, '缺少可核验保留材料，不能声明已保留')
                if payload.material_status == 'missing' and verifiable(row['material']):
                    raise HTTPException(422, '来源失联不等于保留材料缺失')
                now = clock()
                result = {'id': str(uuid4()), 'version': len(history) + 1,
                    'created_at': now.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                    **payload.model_dump(exclude={'expected_version'})}
                connection.execute(insert(evidence_checks).values(id=UUID(result['id']), evidence_id=evidence_id,
                    request_key=idempotency_key, request_body=canonical, version=result['version'], material=result))
                reassess_conclusions(connection, investigation_id, now)
                connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(updated_at=now))
            return result
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, '核验保存结果未确认，请使用同一请求标识重试') from None

    return router
