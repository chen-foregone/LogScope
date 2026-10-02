from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from app.investigations import InvestigationView, investigations
from app.local_access import require_local_operation


class ArchiveChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=0, strict=True)


def archive_router(engine, clock):
    router = APIRouter(prefix='/investigations', dependencies=[Depends(require_local_operation)])

    def change(investigation_id, payload, archived):
        from app.attempts import attempts, current_view
        from app.queries import queries
        try:
            with engine.begin() as connection:
                row = connection.execute(select(investigations).where(
                    investigations.c.id == investigation_id).with_for_update()).mappings().first()
                if row is None:
                    raise HTTPException(404, '调查不存在')
                if row['archive_version'] != payload.expected_version:
                    raise HTTPException(409, '归档状态已变化，请刷新后重试')
                if archived:
                    running = connection.execute(select(attempts.c.id).where(
                        attempts.c.investigation_id == investigation_id,
                        attempts.c.status == '调查中')).first()
                    active = connection.execute(select(queries.c.id).join(attempts,
                        attempts.c.id == queries.c.attempt_id).where(
                        attempts.c.investigation_id == investigation_id, queries.c.active.is_(True))).first()
                    if running or active:
                        raise HTTPException(409, '运行轮次或查询尚未结束，不能归档')
                if (row['archived_at'] is not None) != archived:
                    now = clock()
                    row = connection.execute(update(investigations).where(
                        investigations.c.id == investigation_id).values(
                        archived_at=now if archived else None,
                        retention_expires_at=now + timedelta(days=30) if archived else None,
                        archive_version=row['archive_version'] + 1,
                        updated_at=now).returning(investigations)).mappings().one()
                return current_view(connection, row, clock())
        except SQLAlchemyError:
            raise HTTPException(503, '归档操作结果未确认，请刷新详情核实后再操作') from None

    @router.post('/{investigation_id}/archive', response_model=InvestigationView)
    def archive(investigation_id: UUID, payload: ArchiveChange):
        return change(investigation_id, payload, True)

    @router.post('/{investigation_id}/unarchive', response_model=InvestigationView)
    def unarchive(investigation_id: UUID, payload: ArchiveChange):
        return change(investigation_id, payload, False)

    return router
