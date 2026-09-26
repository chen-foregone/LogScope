import secrets
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select, update

from app.attempts import attempts, executions, current_view
from app.investigations import investigations, InvestigationView


def isolate_lost(connection, row, execution_id, clock):
    from app.deadlines import arbitrate_deadline
    now = clock()
    arbitrate_deadline(connection, row, now)
    connection.execute(update(attempts).where(attempts.c.id == row['id']).values(lost_requested_at=now))
    connection.execute(update(executions).where(executions.c.id == execution_id).values(valid=False))
    connection.execute(update(investigations).where(investigations.c.id == row['investigation_id']).values(updated_at=now))
    # 使用持锁旧行判断既有归属；本事务新写lost不能抢过最终采样已到的期限。
    arbitrate_deadline(connection, row, clock())


def coordinate_interruptions(engine, clock):
    from app.processes import observe_process
    with engine.connect() as connection:
        candidates = connection.execute(select(attempts.c.id, attempts.c.investigation_id).where(
            attempts.c.status == '调查中')).all()
    for attempt_id, investigation_id in candidates:
        with engine.begin() as connection:
            parent = connection.execute(select(investigations.c.id).where(
                investigations.c.id == investigation_id).with_for_update(skip_locked=True)).first()
            if parent is None:
                continue
            row = connection.execute(select(attempts).where(attempts.c.id == attempt_id)
                                     .with_for_update()).mappings().one()
            if row['status'] != '调查中':
                continue
            execution = connection.execute(select(executions).where(executions.c.attempt_id == attempt_id)).mappings().one()
            if execution['stop_protocol'] != 'controlled_queries_v1':
                continue
            observation = observe_process(execution['process_identity'])
            if row['lost_requested_at'] is None:
                from app.queries import queries
                carriers = connection.execute(select(queries.c.process_identity).where(
                    queries.c.attempt_id == attempt_id, queries.c.active.is_(True))).scalars().all()
                if observation['state'] != 'exited' and not any(observe_process(identity)['state'] == 'exited' for identity in carriers):
                    continue
                isolate_lost(connection, row, execution['id'], clock)
                # 隔离先提交，本轮绝不同时宣布退出。
                continue
            if execution['valid'] or observation['state'] != 'exited':
                continue
            from app.queries import queries, release_source_gates
            try:
                with connection.begin_nested():
                    active = connection.execute(select(queries).where(queries.c.attempt_id == attempt_id,
                                                                       queries.c.active.is_(True))).mappings().all()
                    observations = [observation] + [observe_process(query['process_identity']) for query in active]
                    if any(item['state'] != 'exited' for item in observations):
                        continue
                    for query in active:
                        release_source_gates(connection, query['id'], row['source_ids'], query['material']['sources'])
                        material = dict(query['material'], status='interrupted')
                        connection.execute(update(queries).where(queries.c.id == query['id']).values(active=False, material=material))
                    now = clock()
                    reason = ('user_canceled' if row['cancel_requested_at'] else 'attempt_deadline_exceeded'
                              if row['timeout_requested_at'] else 'service_interrupted')
                    connection.execute(update(attempts).where(attempts.c.id == attempt_id).values(
                        status='已取消' if row['cancel_requested_at'] else '失败', ended_at=now, result=None,
                        end_reason=reason, stop_confirmed_at=now, stop_confirmation='process_exit_and_writes_fenced',
                        interruption_confirmation={'processes': observations}))
                    connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(
                        updated_at=now, retention_expires_at=now + timedelta(days=30)))
            except Exception:
                # 仅回滚本候选确认；连接失效或外层提交失败仍向上传播，不能假报确认成功。
                if connection.invalidated:
                    raise


def interruption_router(engine, credential, clock):
    router = APIRouter(prefix='/executor', include_in_schema=False)

    @router.post('/investigations/{investigation_id}/attempts/{attempt_id}/executions/{execution_id}/lost', response_model=InvestigationView)
    async def lost(investigation_id: UUID, attempt_id: UUID, execution_id: UUID, request: Request,
                   authorization: Annotated[str | None, Header()] = None):
        if not credential or not authorization or not secrets.compare_digest(
                authorization.encode(), f'Bearer {credential}'.encode()):
            raise HTTPException(401, '受控执行凭证无效')
        if request.query_params or await request.body():
            raise HTTPException(422, '失联信号不接收客户端退出证明或自由状态')
        try:
            with engine.begin() as connection:
                parent = connection.execute(select(investigations).where(
                    investigations.c.id == investigation_id).with_for_update()).mappings().first()
                row = connection.execute(select(attempts).join(executions,
                    executions.c.attempt_id == attempts.c.id).where(attempts.c.id == attempt_id,
                    attempts.c.investigation_id == investigation_id, executions.c.id == execution_id)
                    .with_for_update()).mappings().first()
                if parent is None or row is None:
                    raise HTTPException(403, '执行身份或轮次无效')
                if row['status'] == '调查中' and row['lost_requested_at'] is None:
                    isolate_lost(connection, row, execution_id, clock)
                parent = connection.execute(select(investigations).where(investigations.c.id == investigation_id)).mappings().one()
                result = current_view(connection, parent, clock())
            return result
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, '执行隔离结果未确认，请读取本轮状态') from None

    return router
