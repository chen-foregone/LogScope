from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, update

from app.attempts import attempts, executions, current_view
from app.investigations import investigations, InvestigationView


def confirm_stop(connection, investigation_id, attempt_id, now):
    from app.queries import queries
    row = connection.execute(select(attempts).where(attempts.c.id == attempt_id)).mappings().one()
    if row['lost_requested_at'] is not None:
        return
    if row['status'] != '调查中' or (row['cancel_requested_at'] is None and row['timeout_requested_at'] is None):
        return
    if connection.execute(select(queries.c.id).where(queries.c.attempt_id == attempt_id,
                                                     queries.c.active.is_(True))).first():
        return
    protocol = connection.execute(select(executions.c.stop_protocol).where(
        executions.c.attempt_id == attempt_id)).scalar_one_or_none()
    if protocol != 'controlled_queries_v1':
        return
    # 持久协议标识证明该执行器不启动查询之外的活动；旧/未知执行器留给失联恢复。
    if row['timeout_requested_at']:
        now = max(now, row['timeout_requested_at'])
    connection.execute(update(attempts).where(attempts.c.id == attempt_id).values(
        status='已取消' if row['cancel_requested_at'] else '失败', ended_at=now, result=None,
        end_reason='user_canceled' if row['cancel_requested_at'] else 'attempt_deadline_exceeded',
        stop_confirmed_at=now, stop_confirmation='controlled_queries_exited_and_writes_fenced'))
    connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(
        updated_at=now, retention_expires_at=None))


def cancellation_router(engine, clock):
    router = APIRouter(prefix='/investigations')

    @router.post('/{investigation_id}/attempts/{attempt_id}/cancel', response_model=InvestigationView)
    async def cancel(investigation_id: UUID, attempt_id: UUID, request: Request):
        if request.query_params or await request.body():
            raise HTTPException(422, '取消仅接受路径中的调查及目标轮次')
        try:
            with engine.begin() as connection:
                parent = connection.execute(select(investigations).where(
                    investigations.c.id == investigation_id).with_for_update()).mappings().first()
                row = connection.execute(select(attempts).where(attempts.c.id == attempt_id,
                    attempts.c.investigation_id == investigation_id).with_for_update()).mappings().first()
                if parent is None or row is None:
                    raise HTTPException(404, '调查轮次不存在')
                from app.deadlines import arbitrate_deadline, finalize_deadline
                timed_out = arbitrate_deadline(connection, row, clock())
                pending = connection.begin_nested()
                if row['status'] == '调查中' and row['cancel_requested_at'] is None and row['lost_requested_at'] is None and not timed_out:
                    now = clock()
                    connection.execute(update(attempts).where(attempts.c.id == attempt_id).values(cancel_requested_at=now))
                    connection.execute(update(executions).where(executions.c.attempt_id == attempt_id).values(valid=False))
                    connection.execute(update(investigations).where(investigations.c.id == investigation_id).values(updated_at=now))
                try:
                    with connection.begin_nested():
                        confirm_stop(connection, investigation_id, attempt_id, clock())
                except Exception:
                    if timed_out:
                        connection.commit()
                    raise
                if timed_out:
                    pending.commit()
                else:
                    timed_out = finalize_deadline(connection, row, clock, pending, reject=False)
                    if timed_out:
                        try:
                            with connection.begin_nested():
                                confirm_stop(connection, investigation_id, attempt_id, clock())
                        except Exception:
                            connection.commit()
                            raise
                parent = connection.execute(select(investigations).where(investigations.c.id == investigation_id)).mappings().one()
                result = current_view(connection, parent, clock())
            return result
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, '取消结果未确认，请读取本轮状态后重试') from None

    return router
