"""Atomic investigation-owned cleanup; never calls a log-source adapter."""
import asyncio
import hashlib
import json
from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import cast, delete, select, tuple_, update
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.exc import SQLAlchemyError

from app.investigations import investigations
from app.local_access import require_local_operation


class CleanupConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope_version: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirm: Literal[True]


def owned_scope(connection, investigation_id):
    from app.attempts import attempts, executions
    from app.evidence import evidence, attempt_evidence, redaction_keys
    from app.conclusions import conclusions
    from app.verification import evidence_checks, conclusion_checks
    from app.severity import severity_events
    from app.queries import queries
    rounds = select(attempts.c.id).where(attempts.c.investigation_id == investigation_id)
    items = select(evidence.c.id).where(evidence.c.attempt_id.in_(rounds))
    findings = select(conclusions.c.id).where(conclusions.c.attempt_id.in_(rounds))
    # Child-first deletion, including the attempt -> historical conclusion cycle.
    predicates = [
        (conclusion_checks, conclusion_checks.c.conclusion_id.in_(findings)),
        (evidence_checks, evidence_checks.c.evidence_id.in_(items)),
        (attempt_evidence, attempt_evidence.c.attempt_id.in_(rounds)),
        (queries, queries.c.attempt_id.in_(rounds)),
        (severity_events, severity_events.c.investigation_id == investigation_id),
        (conclusions, conclusions.c.attempt_id.in_(rounds)),
        (evidence, evidence.c.attempt_id.in_(rounds)),
        (executions, executions.c.attempt_id.in_(rounds)),
        (attempts, attempts.c.investigation_id == investigation_id),
        (redaction_keys, redaction_keys.c.investigation_id == investigation_id),
        (investigations, investigations.c.id == investigation_id),
    ]
    from app.agent_storage import runs, calls, checkpoints
    run_ids = select(runs.c.id).where(runs.c.attempt_id.in_(rounds))
    predicates = [(checkpoints, checkpoints.c.run_id.in_(run_ids)),
                  (calls, calls.c.run_id.in_(run_ids)),
                  (runs, runs.c.attempt_id.in_(rounds))] + predicates
    scope = [(table, predicate, [dict(row) for row in connection.execute(
        select(table).where(predicate)).mappings()]) for table, predicate in predicates]
    return scope


def check_scope(connection, row, scope):
    from app.attempts import attempts
    from app.evidence import attempt_evidence
    from app.queries import source_gates
    rows = {table.name: values for table, _, values in scope}
    if (any(item['status'] == '调查中' for item in rows['attempts']) or
            any(item['active'] for item in rows['controlled_queries']) or
            any(item['valid'] for item in rows['controlled_executions'])):
        raise HTTPException(409, '运行轮次、执行或查询尚未结束，不能清除')
    if any(item['active'] for item in rows.get('agent_runs', [])):
        raise HTTPException(409, 'Agent 尚未安全退出，不能清除')
    round_ids = {item['id'] for item in rows['attempts']}
    evidence_ids = {item['id'] for item in rows['evidence']}
    conclusion_ids = {item['id'] for item in rows['conclusions']}
    if connection.execute(select(attempt_evidence.c.attempt_id).where(
            attempt_evidence.c.evidence_id.in_(evidence_ids),
            attempt_evidence.c.attempt_id.not_in(round_ids))).first():
        raise HTTPException(409, '其他调查仍引用材料，本次清除全部拒绝')
    if connection.execute(select(attempts.c.id).where(
            attempts.c.reverify_conclusion_id.in_(conclusion_ids),
            attempts.c.investigation_id != row['id'])).first():
        raise HTTPException(409, '其他调查仍引用结论，本次清除全部拒绝')
    query_ids = {item['id'] for item in rows['controlled_queries']}
    if connection.execute(select(source_gates.c.source_key).where(
            source_gates.c.query_id.in_(query_ids))).first():
        raise HTTPException(409, '来源控制仍关联本调查查询，需核查后重试；未清除共享控制')
    # JSON materials can contain evidence/conclusion references without foreign keys.
    protected = {str(value) for value in evidence_ids | conclusion_ids | round_ids}
    from app.investigations import metadata
    for table in metadata.tables.values():
        if 'material' not in table.c or not protected:
            continue
        own_ids = {item['id'] for item in rows.get(table.name, [])}
        path = '$.** ? (' + ' || '.join('@ == ' + json.dumps(value) for value in sorted(protected)) + ')'
        if connection.execute(select(table.c.id).where(table.c.id.not_in(own_ids),
                cast(table.c.material, JSONB).op('@?')(cast(path, JSONPATH))).limit(1)).first():
            raise HTTPException(409, '其他调查仍引用材料，本次清除全部拒绝')


def scope_view(scope):
    serializable = {table.name: sorted(values, key=lambda row: json.dumps(row, sort_keys=True, default=str))
                    for table, _, values in scope}
    fingerprint = hashlib.sha256(json.dumps(serializable, sort_keys=True, default=str).encode()).hexdigest()
    return {'scope_version': fingerprint, 'counts': {name: len(rows) for name, rows in serializable.items()},
            'irreversible': True, 'source_logs_affected': False}


def locked_investigation(connection, investigation_id):
    row = connection.execute(select(investigations).where(
        investigations.c.id == investigation_id).with_for_update()).mappings().first()
    if row is None:
        raise HTTPException(404, '调查已不存在；无法证明此前请求是否成功，不可恢复')
    return row


def clear_locked(connection, row, scope):
    from app.attempts import attempts
    view = scope_view(scope)
    check_scope(connection, row, scope)
    connection.execute(update(attempts).where(attempts.c.investigation_id == row['id'])
                       .values(reverify_conclusion_id=None))
    for table, predicate, _ in scope:
        connection.execute(delete(table).where(predicate))
    return {'deleted_counts': view['counts'], 'deleted': True}


def cleanup_router(engine, clock):
    router = APIRouter(prefix='/investigations', dependencies=[Depends(require_local_operation)])

    @router.post('/{investigation_id}/cleanup-preview')
    def preview(investigation_id: UUID):
        try:
            with engine.begin() as connection:
                row = locked_investigation(connection, investigation_id)
                scope = owned_scope(connection, investigation_id)
                check_scope(connection, row, scope)
                return scope_view(scope)
        except SQLAlchemyError:
            raise HTTPException(503, '清除预览读取失败，未执行删除') from None

    @router.post('/{investigation_id}/cleanup')
    def confirm(investigation_id: UUID, payload: CleanupConfirmation):
        try:
            with engine.begin() as connection:
                row = locked_investigation(connection, investigation_id)
                scope = owned_scope(connection, investigation_id)
                if scope_view(scope)['scope_version'] != payload.scope_version:
                    raise HTTPException(409, '清除范围或状态已变化，请重新预览')
                result = clear_locked(connection, row, scope)
            return result
        except SQLAlchemyError:
            raise HTTPException(503, '清除结果未确认，请刷新核查；不存在不代表可证明此前请求成功') from None

    return router


def clear_expired_archives(engine, clock):
    """Every candidate is rechecked under the same investigation row lock."""
    now = clock()
    cursor = None
    while True:
        with engine.connect() as connection:
            query = select(investigations.c.id, investigations.c.retention_expires_at).where(
                investigations.c.archived_at.is_not(None), investigations.c.retention_expires_at <= now)
            if cursor is not None:
                query = query.where(tuple_(investigations.c.retention_expires_at, investigations.c.id) > tuple_(*cursor))
            candidates = connection.execute(query.order_by(
                investigations.c.retention_expires_at, investigations.c.id).limit(100)).all()
        if not candidates:
            break
        for investigation_id, _ in candidates:
            _clear_expired_candidate(engine, clock, investigation_id)
        cursor = (candidates[-1].retention_expires_at, candidates[-1].id)


def _clear_expired_candidate(engine, clock, investigation_id):
    try:
        with engine.begin() as connection:
            row = locked_investigation(connection, investigation_id)
            if (row['archived_at'] is None or row['retention_expires_at'] is None or
                    row['retention_expires_at'] != row['archived_at'] + timedelta(days=30) or
                    clock() < row['retention_expires_at']):
                return
            clear_locked(connection, row, owned_scope(connection, investigation_id))
    except (HTTPException, SQLAlchemyError):
        # No receipt or identifying log; preserved rows are retried by a later sweep.
        return


async def cleanup_coordinator(engine, clock, *, interval=60):
    while True:
        await asyncio.sleep(interval)
        running = asyncio.create_task(asyncio.to_thread(clear_expired_archives, engine, clock))
        try:
            await asyncio.shield(running)
        except asyncio.CancelledError:
            # A canceled wrapper does not stop its database thread; drain before disposing the engine.
            while not running.done():
                try:
                    await asyncio.shield(running)
                except (asyncio.CancelledError, Exception):
                    pass
            if not running.cancelled():
                running.exception()
            raise
        except SQLAlchemyError:
            pass
