import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select

from app.attempts import attempts, executions


def execution_router(engine, credential):
    router = APIRouter(prefix="/executor", include_in_schema=False)

    def authorize(authorization):
        if not credential or not authorization or not secrets.compare_digest(authorization.encode('utf-8'), f"Bearer {credential}".encode('utf-8')):
            raise HTTPException(401, "受控执行凭证无效")

    @router.get("/investigations/{investigation_id}/attempts/{attempt_id}/executions")
    def accepted(investigation_id: UUID, attempt_id: UUID,
                 authorization: Annotated[str | None, Header()] = None):
        authorize(authorization)
        with engine.connect() as connection:
            rows = connection.execute(select(executions.c.id, executions.c.attempt_id).join(
                attempts, attempts.c.id == executions.c.attempt_id).where(
                attempts.c.investigation_id == investigation_id, attempts.c.id == attempt_id,
                attempts.c.status == "调查中", executions.c.valid.is_(True))).mappings().all()
        if not rows:
            raise HTTPException(403, "执行身份或轮次无效")
        return {"executions": [dict(row) for row in rows]}

    @router.get("/investigations/{investigation_id}/attempts/{attempt_id}/executions/{execution_id}/context")
    def context(investigation_id: UUID, attempt_id: UUID, execution_id: UUID,
                authorization: Annotated[str | None, Header()] = None):
        authorize(authorization)
        with engine.connect() as connection:
            row = connection.execute(select(attempts).join(executions, executions.c.attempt_id == attempts.c.id).where(
                attempts.c.investigation_id == investigation_id, attempts.c.id == attempt_id,
                executions.c.id == execution_id, executions.c.valid.is_(True),
                attempts.c.status == "调查中")).mappings().first()
        if row is None:
            raise HTTPException(403, "执行身份或轮次无效")
        return {"investigation_id": investigation_id, "attempt_id": attempt_id, "execution_id": execution_id,
                "service_id": row["service_id"], "source_ids": row["source_ids"],
                "starts_at": row["starts_at"], "ends_at": row["ends_at"],
                "execution_mode": "controlled", "capabilities": ["read_execution_context"], "environment_tools": []}

    return router
