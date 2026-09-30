import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine

from app.api.health import router as health_router
from app.core.config import settings
from app.investigations import investigation_router


def create_app(database_url: str | None = None, *, services_file=None, executor_credential=None, execution_adapter=None, clock=None, query_sources=None, query_schedule=None, deadline_schedule=None) -> FastAPI:
    from app.services import load_services, service_router
    services = load_services(services_file if services_file is not None else settings.services_file)
    if query_sources is None and settings.simulated_logs_url:
        from app.log_sources import SimulatedLogSource
        query_sources = {source_id: SimulatedLogSource(settings.simulated_logs_url)
                         for service in services.values() for source_id in service.source_ids}
    engine = create_engine(database_url or settings.database_url, pool_pre_ping=True, hide_parameters=True)

    from datetime import datetime, timezone
    from app.deadlines import DeadlineSchedule, deadline_coordinator
    import asyncio
    business_clock = clock or (lambda: datetime.now(timezone.utc))

    @asynccontextmanager
    async def lifespan(app):
        coordinator = asyncio.create_task(deadline_coordinator(engine, business_clock, deadline_schedule or DeadlineSchedule()))
        try:
            yield
        finally:
            coordinator.cancel()
            await asyncio.gather(coordinator, return_exceptions=True)
            engine.dispose()

    application = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request, exc):
        # 不回显非法原始输入，避免孤立代理码点使错误响应编码失败。
        detail = [{"type": "request_invalid", "loc": ["request"],
                   "msg": "请求参数无效；日志范围须带时区且不超过24小时，不接受未知字段"}]
        return Response(status_code=422, media_type="application/json",
                        content=json.dumps({"detail": detail}, ensure_ascii=True))

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )
    from app.attempts import ControlledExecutionAdapter, attempt_router
    from app.execution import execution_router
    from app.interruption import interruption_router
    application.include_router(interruption_router(engine, executor_credential if executor_credential is not None else settings.executor_credential, business_clock), prefix=settings.api_prefix)
    from app.evidence import evidence_router, reference_router
    application.include_router(reference_router(engine, business_clock, executor_credential if executor_credential is not None else settings.executor_credential), prefix=settings.api_prefix)
    from app.severity import severity_router
    application.include_router(severity_router(engine, executor_credential if executor_credential is not None else settings.executor_credential, business_clock), prefix=settings.api_prefix)
    from app.conclusions import conclusion_router
    from app.verification import verification_router
    application.include_router(verification_router(engine, executor_credential if executor_credential is not None else settings.executor_credential,
        business_clock), prefix=settings.api_prefix)
    from datetime import datetime, timezone
    from app.queries import QuerySchedule, query_router
    application.include_router(query_router(engine, executor_credential if executor_credential is not None else settings.executor_credential,
        query_sources or {}, clock or (lambda: datetime.now(timezone.utc)), query_schedule or QuerySchedule()), prefix=settings.api_prefix)
    application.include_router(conclusion_router(engine, executor_credential if executor_credential is not None else settings.executor_credential,
        clock or (lambda: datetime.now(timezone.utc))), prefix=settings.api_prefix)
    application.include_router(evidence_router(engine, executor_credential if executor_credential is not None else settings.executor_credential,
        clock or (lambda: datetime.now(timezone.utc))), prefix=settings.api_prefix)
    application.include_router(execution_router(engine, executor_credential if executor_credential is not None else settings.executor_credential, business_clock), prefix=settings.api_prefix)
    adapter = execution_adapter or ControlledExecutionAdapter()
    from app.cancellation import cancellation_router
    application.include_router(cancellation_router(engine, clock or (lambda: datetime.now(timezone.utc))), prefix=settings.api_prefix)
    application.include_router(attempt_router(engine, services, adapter, clock or (lambda: datetime.now(timezone.utc))), prefix=settings.api_prefix)
    application.include_router(service_router(services), prefix=settings.api_prefix)
    application.include_router(health_router, prefix=settings.api_prefix)
    application.include_router(investigation_router(engine, business_clock), prefix=settings.api_prefix)

    @application.get("/")
    def root() -> dict[str, str]:
        return {"service": "logscope-api", "docs": "/docs"}

    return application


app = create_app()
