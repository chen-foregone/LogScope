import asyncio
import math
import inspect
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request, Response
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import String, Boolean, Column, DateTime, ForeignKey, JSON, Table, Uuid, insert, select, update

from app.attempts import attempts, executions
from app.evidence import redaction_keys
from app.investigations import investigations, metadata

source_gates = Table('query_source_gates', metadata,
    Column('source_key', String(64), primary_key=True), Column('query_id', Uuid),
    Column('blocked', Boolean), Column('retry_at', DateTime(timezone=True)))

queries = Table('controlled_queries', metadata,
    Column('id', Uuid, primary_key=True), Column('attempt_id', Uuid, ForeignKey('attempts.id')),
    Column('execution_id', Uuid, ForeignKey('controlled_executions.id')),
    Column('request_key', Uuid), Column('active', Boolean), Column('created_at', DateTime(timezone=True)),
    Column('material', JSON), Column('process_identity', JSON))


def release_source_gates(connection, query_id, source_ids, states):
    states_by_key = {hashlib.sha256(source_id.encode()).hexdigest(): state
                     for source_id, state in zip(source_ids, states)}
    gates = connection.execute(select(source_gates).where(source_gates.c.query_id == query_id)
        .order_by(source_gates.c.source_key).with_for_update()).mappings().all()
    for gate in gates:
        state = states_by_key.get(gate['source_key'], {})
        values = {'query_id': None}
        if state.get('reason') == 'rate_limited':
            retry = state.get('retry_not_before')
            retry_at = datetime.fromisoformat(retry) if retry else None
            if gate['blocked']:
                retry_at = (max(gate['retry_at'], retry_at)
                            if gate['retry_at'] is not None and retry_at is not None else None)
            values.update(blocked=True, retry_at=retry_at)
        connection.execute(update(source_gates).where(source_gates.c.source_key == gate['source_key'],
            source_gates.c.query_id == query_id).values(**values))


def query_views(connection, attempt_id):
    return list(connection.execute(select(queries.c.material).where(queries.c.attempt_id == attempt_id)
                                   .order_by(queries.c.created_at, queries.c.id)).scalars())


def source_aliases(key, source_ids):
    return ['来源代号-' + hmac.new(key, json.dumps(['source', s], ensure_ascii=True).encode(), hashlib.sha256).hexdigest()
            for s in source_ids]


def coverage_aliases(connection, attempt_id, source_ids):
    key = connection.execute(select(redaction_keys.c.secret).join(attempts,
        attempts.c.investigation_id == redaction_keys.c.investigation_id).where(attempts.c.id == attempt_id)).scalar_one()
    return source_aliases(key, source_ids)


def query_coverage(history, aliases):
    sources = history[-1]['sources'] if history else []
    missing = [alias for index, alias in enumerate(aliases)
               if index >= len(sources) or sources[index]['state'] != 'succeeded']
    return {'complete': bool(aliases) and not missing, 'missing_sources': missing,
            'notice': '查询成功不表示没有异常；查询状态不等于证据覆盖。' if sources and not missing else
                      '覆盖不完整：来源尚未查询或不可用，不能据此排除原因；查不到不表示没有异常。'}


class SourceFailure(Exception):
    """仅由服务端受信适配器产生；禁止从请求体接收故障分类。"""
    def __init__(self, reason, *, retry_after=None):
        self.reason = reason if reason in {'network', 'timeout', 'permission', 'credentials', 'parameters', 'rate_limited', 'not_configured'} else 'adapter_error'
        self.retry_after = retry_after
        super().__init__('来源查询不可用')


async def wait_for_exit(task):
    if not task.done() and not task.cancelling():
        task.cancel()
    while not task.done():
        try:
            await asyncio.shield(task)
        except (asyncio.CancelledError, Exception):
            pass
    # 取回异常，避免已退出任务的异常无人读取；调用方决定如何处理结果。
    if not task.cancelled():
        task.exception()


class QuerySchedule:
    async def wait(self, seconds):
        await asyncio.sleep(seconds)

    async def deadline(self, operation, seconds):
        task = asyncio.create_task(operation)
        try:
            done, _ = await asyncio.wait({task}, timeout=seconds)
            if done:
                return task.result()
            await wait_for_exit(task)
            if not task.cancelled():
                error = task.exception()
                if isinstance(error, SourceFailure) and error.reason == 'rate_limited':
                    raise error
            # 即使来源吞掉取消并返回，也不能把越界结果算成功。
            raise TimeoutError()
        except asyncio.CancelledError:
            await wait_for_exit(task)
            if not task.cancelled():
                error = task.exception()
                if isinstance(error, SourceFailure) and error.reason == 'rate_limited':
                    raise error
            raise
        finally:
            await wait_for_exit(task)


class QueryCanceled(Exception):
    pass


def query_router(engine, credential, sources, clock, schedule):
    router = APIRouter(prefix='/executor', include_in_schema=False)

    @router.post('/investigations/{investigation_id}/attempts/{attempt_id}/executions/{execution_id}/queries', status_code=201)
    async def run(investigation_id: UUID, attempt_id: UUID, execution_id: UUID, response: Response, request: Request,
                  idempotency_key: Annotated[UUID, Header()],
                  authorization: Annotated[str | None, Header()] = None):
        if not credential or not authorization or not secrets.compare_digest(
                authorization.encode(), f'Bearer {credential}'.encode()):
            raise HTTPException(401, '受控执行凭证无效')
        if request.query_params:
            raise HTTPException(422, '查询不接受客户端参数，仅使用本轮快照')
        async for chunk in request.stream():
            if chunk:
                raise HTTPException(422, '查询不接受请求体、来源地址或错误分类，仅使用本轮快照')
        return await execute_query(engine, sources, clock, schedule, investigation_id, attempt_id,
                                   execution_id, response, idempotency_key)

    return router


async def execute_query(engine, sources, clock, schedule, investigation_id, attempt_id,
                        execution_id, response, idempotency_key):
    collection_failed = False
    collection_source_id = None
    try:
        with engine.begin() as connection:
            connection.execute(select(investigations.c.id).where(investigations.c.id == investigation_id).with_for_update()).first()
            row = connection.execute(select(attempts).join(executions, executions.c.attempt_id == attempts.c.id).where(
                attempts.c.investigation_id == investigation_id, attempts.c.id == attempt_id,
                executions.c.id == execution_id, executions.c.valid.is_(True), attempts.c.status == '调查中')
                .with_for_update()).mappings().first()
            if row is None:
                raise HTTPException(403, '执行身份或轮次无效')
            from app.deadlines import enforce_deadline, finalize_deadline
            enforce_deadline(connection, row, clock())
            pending = connection.begin_nested()
            replay = connection.execute(select(queries).where(queries.c.attempt_id == attempt_id,
                queries.c.request_key == idempotency_key)).mappings().first()
            if replay:
                if replay['execution_id'] != execution_id:
                    raise HTTPException(409, '查询请求身份冲突')
                finalize_deadline(connection, row, clock, pending)
                response.status_code = 202 if replay['active'] else 200
                return replay['material']
            if connection.execute(select(queries.c.id).where(queries.c.attempt_id == attempt_id, queries.c.active.is_(True))).first():
                raise HTTPException(409, '本轮查询尚未退出，不能并行重复查询')
            key = connection.execute(select(redaction_keys.c.secret).where(redaction_keys.c.investigation_id == investigation_id)).scalar_one_or_none()
            if key is None:
                key = secrets.token_bytes(32)
                connection.execute(insert(redaction_keys).values(investigation_id=investigation_id, secret=key))
            aliases = source_aliases(key, row['source_ids'])
            identity = uuid4()
            source_keys = {s: hashlib.sha256(s.encode()).hexdigest() for s in row['source_ids']}
            skipped = {}
            # 来源级持久闸门跨调查/进程生效；只在接纳短事务持锁，不跨I/O。
            for source_key in sorted(source_keys.values()):
                connection.execute(pg_insert(source_gates).values(source_key=source_key, blocked=False)
                                   .on_conflict_do_nothing())
                gate = connection.execute(select(source_gates).where(source_gates.c.source_key == source_key)
                                          .with_for_update()).mappings().one()
                if gate['query_id'] is not None or (gate['blocked'] and (
                        gate['retry_at'] is None or clock() < gate['retry_at'])):
                    skipped[source_key] = {
                        'state': 'skipped', 'reason': 'inflight' if gate['query_id'] is not None else 'rate_limited',
                        'retry_not_before': gate['retry_at'].isoformat() if gate['retry_at'] else None}
                    continue
                connection.execute(update(source_gates).where(source_gates.c.source_key == source_key)
                                   .values(query_id=identity, blocked=False, retry_at=None))
            result = {'id': str(identity), 'status': 'running', 'sources': [
                {'source': alias, 'state': 'pending', 'attempts': 0, 'reason': None, 'failures': [],
                 **({'mode': 'simulated', 'protocol': 'simulated-logs-v1'}
                    if getattr(sources.get(source_id), 'mode', None) == 'simulated' else {}),
                 **skipped.get(source_keys[source_id], {})} for source_id, alias in zip(row['source_ids'], aliases)]}
            if len(skipped) == len(source_keys):
                raise HTTPException(409, {'code': 'sources_blocked',
                    'notice': '所有来源在途或受限，未发起查询；有恢复时间的来源到期后可试，其余须等待退出或由来源确认恢复。',
                    'sources': result['sources']})
            from app.processes import current_process
            connection.execute(insert(queries).values(id=identity, attempt_id=attempt_id, execution_id=execution_id,
                process_identity=current_process(), request_key=idempotency_key, active=True, created_at=clock(), material=result))
            connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(updated_at=clock()))
            finalize_deadline(connection, row, clock, pending)
        total_end = asyncio.get_running_loop().time() + max(0, (row['deadline_at'] - clock()).total_seconds())

        def stop_requested():
            with engine.begin() as connection:
                connection.execute(select(investigations.c.id).where(
                    investigations.c.id == investigation_id).with_for_update()).first()
                current = connection.execute(select(attempts).where(attempts.c.id == attempt_id)
                                             .with_for_update()).mappings().one()
                from app.deadlines import arbitrate_deadline
                observed_now = clock()
                if asyncio.get_running_loop().time() >= total_end:
                    observed_now = max(observed_now, current['deadline_at'])
                timed_out = arbitrate_deadline(connection, current, observed_now)
                return current['cancel_requested_at'] is not None or current['lost_requested_at'] is not None or timed_out

        async def controlled(operation):
            canceled = stop_requested()
            if canceled:
                raise QueryCanceled()
            task = asyncio.create_task(operation())
            try:
                while True:
                    done, _ = await asyncio.wait({task}, timeout=0.1)
                    canceled = stop_requested()
                    if canceled:
                        await wait_for_exit(task)
                        if not task.cancelled():
                            failure = task.exception()
                            if isinstance(failure, SourceFailure) and failure.reason == 'rate_limited':
                                state.update(state='unavailable', reason='rate_limited', retry_not_before=None)
                                requested = failure.retry_after
                                if type(requested) in (int, float) and math.isfinite(requested) and requested > 0:
                                    state['retry_after_seconds'] = requested
                                    try:
                                        state['retry_not_before'] = (clock() + timedelta(seconds=requested)).isoformat()
                                    except OverflowError:
                                        pass
                                state['failures'].append({'reason': 'rate_limited', 'attempt': state['attempts'],
                                    'timeout_seconds': 30, 'wait_seconds': None,
                                    'retry_after_seconds': state.get('retry_after_seconds')})
                        raise QueryCanceled()
                    if done:
                        return task.result()
            finally:
                await wait_for_exit(task)

        def progress():
            with engine.begin() as connection:
                connection.execute(select(investigations.c.id).where(investigations.c.id == investigation_id).with_for_update()).first()
                current = connection.execute(select(attempts).where(attempts.c.id == attempt_id)
                                             .with_for_update()).mappings().one()
                enforce_deadline(connection, current, max(clock(), row['deadline_at'])
                    if asyncio.get_running_loop().time() >= total_end else clock())
                valid = connection.execute(select(executions.c.id).join(attempts, attempts.c.id == executions.c.attempt_id).where(
                    executions.c.id == execution_id, executions.c.valid.is_(True), attempts.c.status == '调查中')).first()
                if valid is None:
                    raise HTTPException(403, '执行身份或轮次无效')
                pending = connection.begin_nested()
                connection.execute(update(queries).where(queries.c.id == identity).values(active=True, material=result))
                connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(updated_at=clock()))
                finalize_deadline(connection, current, lambda: max(clock(), row['deadline_at'])
                    if asyncio.get_running_loop().time() >= total_end else clock(), pending)

        for source_id, state in zip(row['source_ids'], result['sources']):
            if state['state'] == 'skipped':
                continue
            for number in range(1, 4):
                state.update(state='querying', attempts=number, retry_after_seconds=None)
                progress()
                try:
                    adapter = sources.get(source_id)
                    if adapter is None:
                        state['attempts'] = 0
                        raise SourceFailure('not_configured')
                    if not inspect.iscoroutinefunction(adapter.query):
                        state['attempts'] = 0
                        raise SourceFailure('adapter_error')
                    remaining = max(0, min(total_end - asyncio.get_running_loop().time(),
                                           (row['deadline_at'] - clock()).total_seconds()))
                    if remaining <= 0:
                        raise TimeoutError()
                    output = await controlled(lambda: schedule.deadline(adapter.query(source_id=source_id,
                        starts_at=row['starts_at'], ends_at=row['ends_at']), min(30, remaining)))
                    from app.log_sources import CollectedLogs
                    if isinstance(output, CollectedLogs):
                        from app.evidence import Material, save_material
                        from pydantic import ValidationError
                        rejected = False
                        for item in output.items:
                            if stop_requested():
                                raise QueryCanceled()
                            try:
                                encoded = json.dumps(item)
                                if len(encoded.encode('utf-8')) > 65536:
                                    raise ValueError()
                                payload = Material.model_validate_json(encoded)
                            except (ValidationError, ValueError, TypeError):
                                payload = None
                            if payload is not None and payload.source_id != source_id:
                                payload = None
                            if payload is not None:
                                from app.evidence import RecordV2
                                role = getattr(adapter, 'component', None)
                                if (role is not None and isinstance(payload.record, RecordV2)
                                        and payload.record.observation.component != role):
                                    payload = None
                            try:
                                save_material(engine, clock, investigation_id, attempt_id, execution_id, payload, Response(),
                                              source_mode=getattr(adapter, 'mode', None))
                            except HTTPException as error:
                                if error.status_code == 503:
                                    collection_failed = True
                                    collection_source_id = source_id
                                if error.status_code in (403, 503):
                                    raise
                                rejected = True
                                continue
                            await asyncio.sleep(0)
                        if rejected:
                            # 单条拒绝不撤销已提交材料，也不丢弃后续合法记录；来源整体不宣布成功。
                            state.update(state='unavailable', reason='adapter_error')
                            state['failures'].append({'reason': 'adapter_error', 'attempt': number,
                                'timeout_seconds': 30, 'wait_seconds': None, 'retry_after_seconds': None})
                            progress()
                            break
                    elif output is not None:
                        raise SourceFailure('adapter_error')
                except QueryCanceled:
                    raise
                except HTTPException:
                    raise
                except TimeoutError:
                    failure = SourceFailure('timeout')
                except SourceFailure as error:
                    failure = error
                except Exception:
                    failure = SourceFailure('adapter_error')
                else:
                    state.update(state='succeeded', reason=None, retry_not_before=None)
                    progress()
                    break
                state['reason'] = failure.reason
                delay = None
                if number < 3 and failure.reason in {'network', 'timeout'}:
                    delay = (2, 5)[number - 1]
                elif failure.reason == 'rate_limited':
                    state['retry_not_before'] = None
                    requested = failure.retry_after
                    if type(requested) in (int, float) and math.isfinite(requested) and requested > 0:
                        # 只等待明确来源值；过长限制留给人工恢复，不提前重试。
                        state['retry_after_seconds'] = requested
                        try:
                            state['retry_not_before'] = (clock() + timedelta(seconds=requested)).isoformat()
                        except OverflowError:
                            state['retry_not_before'] = None
                        if requested <= 30 and number < 3:
                            delay = requested
                state['failures'].append({'reason': failure.reason, 'attempt': number,
                    'timeout_seconds': 30, 'wait_seconds': delay,
                    'retry_after_seconds': state.get('retry_after_seconds')})
                if delay is None:
                    state['state'] = 'unavailable'
                    progress()
                    break
                state['state'] = 'waiting'
                progress()
                # 每次等待从失败退出后开始，2/5秒不是调用开始偏移。
                await controlled(lambda: schedule.wait(min(delay, max(0, min(
                    total_end - asyncio.get_running_loop().time(), (row['deadline_at'] - clock()).total_seconds())))))
        result['status'] = 'finished'
        with engine.begin() as connection:
            connection.execute(select(investigations.c.id).where(investigations.c.id == investigation_id).with_for_update()).first()
            current = connection.execute(select(attempts).where(attempts.c.id == attempt_id)
                                         .with_for_update()).mappings().one()
            enforce_deadline(connection, current, max(clock(), row['deadline_at'])
                if asyncio.get_running_loop().time() >= total_end else clock())
            valid = connection.execute(select(executions.c.id).join(attempts, attempts.c.id == executions.c.attempt_id).where(
                executions.c.id == execution_id, executions.c.valid.is_(True), attempts.c.status == '调查中')).first()
            if valid is None:
                raise HTTPException(403, '执行身份或轮次无效')
            pending = connection.begin_nested()
            release_source_gates(connection, identity, row['source_ids'], result['sources'])
            connection.execute(update(queries).where(queries.c.id == identity).values(active=False, material=result))
            now = clock().astimezone(timezone.utc)
            values = {'updated_at': now}
            if result['sources'] and all(s['state'] == 'unavailable' for s in result['sources']):
                connection.execute(update(attempts).where(attempts.c.id == attempt_id).values(
                    status='失败', result=None, ended_at=now, end_reason='necessary_sources_unavailable'))
                connection.execute(update(executions).where(executions.c.id == execution_id).values(valid=False))
                values['retention_expires_at'] = None
            connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(**values))
            finalize_deadline(connection, current, lambda: max(clock(), row['deadline_at'])
                if asyncio.get_running_loop().time() >= total_end else clock(), pending)
        return result
    except (QueryCanceled, HTTPException) as error:
        if 'result' in locals():
            try:
                with engine.begin() as connection:
                    connection.execute(select(investigations.c.id).where(
                        investigations.c.id == investigation_id).with_for_update()).first()
                    stopping = connection.execute(select(attempts).where(
                        attempts.c.id == attempt_id).with_for_update()).mappings().first()
                    canceled = stopping and (stopping['cancel_requested_at'] or stopping['timeout_requested_at'] or stopping['lost_requested_at'])
                    owned = connection.execute(select(queries.c.id).where(queries.c.id == identity,
                        queries.c.active.is_(True))).first()
                    if canceled is not None and owned:
                        result['status'] = 'canceled' if stopping['cancel_requested_at'] else 'timed_out' if stopping['timeout_requested_at'] else 'interrupted'
                        release_source_gates(connection, identity, row['source_ids'], result['sources'])
                        for state in result['sources']:
                            if state['state'] not in {'skipped', 'unavailable'}:
                                state.update(state='canceled', reason=None)
                        connection.execute(update(queries).where(queries.c.id == identity).values(active=False, material=result))
                        from app.cancellation import confirm_stop
                        confirm_stop(connection, investigation_id, attempt_id, clock())
                    elif owned and collection_failed:
                        # HTTP collection has exited; preserve evidence but do not pin its source forever.
                        result['status'] = 'failed'
                        for source_id, state in zip(row['source_ids'], result['sources']):
                            if source_id == collection_source_id and state['state'] not in {'skipped', 'unavailable'}:
                                state.update(state='unavailable', reason='adapter_error')
                        release_source_gates(connection, identity, row['source_ids'], result['sources'])
                        connection.execute(update(queries).where(queries.c.id == identity).values(active=False, material=result))
            except Exception:
                raise HTTPException(503, '停止确认未保存，保持正在停止，请读取本轮状态') from None
        if isinstance(error, QueryCanceled):
            raise HTTPException(403, '本轮已请求停止，查询结果不纳入') from None
        raise
    except Exception:
        raise HTTPException(503, '查询结果未确认；未保存原始诊断，请读取本轮状态') from None
