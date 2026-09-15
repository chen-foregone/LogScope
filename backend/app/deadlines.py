import asyncio

from fastapi import HTTPException
from sqlalchemy import select, update

from app.attempts import attempts, executions
from app.investigations import investigations


def arbitrate_deadline(connection, row, now):
    """调用方已持调查及轮次锁；已取得的停止归属与终态不改。"""
    if (row['status'] != '调查中' or row['cancel_requested_at'] is not None
            or row['timeout_requested_at'] is not None or row['lost_requested_at'] is not None or now < row['deadline_at']):
        return row['timeout_requested_at'] is not None
    connection.execute(update(attempts).where(attempts.c.id == row['id']).values(timeout_requested_at=now))
    connection.execute(update(executions).where(executions.c.attempt_id == row['id']).values(valid=False))
    connection.execute(update(investigations).where(investigations.c.id == row['investigation_id']).values(updated_at=now))
    return True


class DeadlineSchedule:
    async def wait(self):
        await asyncio.sleep(0.1)


def coordinate_deadlines(engine, clock):
    from app.interruption import coordinate_interruptions
    coordinate_interruptions(engine, clock)
    from app.cancellation import confirm_stop
    with engine.connect() as connection:
        candidates = connection.execute(select(attempts.c.id, attempts.c.investigation_id).where(
            attempts.c.status == '调查中', attempts.c.deadline_at <= clock())).all()
    for attempt_id, investigation_id in candidates:
        with engine.begin() as connection:
            parent = connection.execute(select(investigations.c.id).where(
                investigations.c.id == investigation_id).with_for_update(skip_locked=True)).first()
            if parent is None:
                continue
            row = connection.execute(select(attempts).where(attempts.c.id == attempt_id)
                                     .with_for_update()).mappings().one()
            arbitrate_deadline(connection, row, clock())
            # 确认失败只回滚确认，不撤销停止归属和写隔离。
            try:
                with connection.begin_nested():
                    confirm_stop(connection, investigation_id, attempt_id, clock())
            except Exception:
                pass


async def deadline_coordinator(engine, clock, schedule):
    while True:
        await schedule.wait()
        running = asyncio.create_task(asyncio.to_thread(coordinate_deadlines, engine, clock))
        try:
            await asyncio.shield(running)
        except asyncio.CancelledError:
            # 不能取消 to_thread 包装任务；done 必须表示数据库轮确已返回。
            while not running.done():
                try:
                    await asyncio.shield(running)
                except (asyncio.CancelledError, Exception):
                    pass
            if not running.cancelled():
                running.exception()
            raise
        except Exception:
            # 数据库故障保持持久活动事实；下一轮重试，不推断执行退出。
            pass


def finalize_deadline(connection, row, clock, pending, *, reject=True):
    # 持锁、业务 SQL 完成后的最后裁决；COMMIT 传输/落盘延迟不属于可原子采样范围。
    now = clock()
    if (row['status'] == '调查中' and row['cancel_requested_at'] is None
            and row['lost_requested_at'] is None and now >= row['deadline_at']):
        pending.rollback()
        if reject:
            enforce_deadline(connection, row, now)
        else:
            return arbitrate_deadline(connection, row, now)
    else:
        pending.commit()
    return row['timeout_requested_at'] is not None


def enforce_deadline(connection, row, now):
    if arbitrate_deadline(connection, row, now):
        # 隔离先独立提交；业务拒绝及其异常不能回滚已取得的超时归属。
        try:
            with connection.begin_nested():
                from app.cancellation import confirm_stop
                confirm_stop(connection, row['investigation_id'], row['id'], now)
        except Exception:
            connection.commit()
            raise HTTPException(503, '超时隔离已保存，退出确认未完成，请读取本轮状态') from None
        connection.commit()
        raise HTTPException(403, '本轮总时限已到，拒绝旧执行及重放产物；等待执行退出确认')
