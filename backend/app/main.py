import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine

from app.api.health import router as health_router
from app.core.config import settings
from app.investigations import investigation_router


def create_app(database_url: str | None = None, *, services_file=None, executor_credential=None, execution_adapter=None) -> FastAPI:
    from app.services import load_services, service_router
    services = load_services(services_file if services_file is not None else settings.services_file)
    engine = create_engine(database_url or settings.database_url, pool_pre_ping=True, hide_parameters=True)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    application = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request, exc):
        # 不回显非法原始输入，避免孤立代理码点使错误响应编码失败。
        detail = [{"type": error["type"], "loc": error["loc"], "msg": error["msg"]}
                  for error in exc.errors()]
        return Response(status_code=422, media_type="application/json",
                        content=json.dumps({"detail": detail}, ensure_ascii=True))

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )
    from app.attempts import ControlledExecutionAdapter, attempt_router
    from app.execution import execution_router
    from app.evidence import evidence_router
    application.include_router(evidence_router(engine, executor_credential if executor_credential is not None else settings.executor_credential), prefix=settings.api_prefix)
    application.include_router(execution_router(engine, executor_credential if executor_credential is not None else settings.executor_credential), prefix=settings.api_prefix)
    application.include_router(attempt_router(engine, services, execution_adapter or ControlledExecutionAdapter()), prefix=settings.api_prefix)
    application.include_router(service_router(services), prefix=settings.api_prefix)
    application.include_router(health_router, prefix=settings.api_prefix)
    application.include_router(investigation_router(engine), prefix=settings.api_prefix)

    @application.get("/")
    def root() -> dict[str, str]:
        return {"service": "logscope-api", "docs": "/docs"}

    return application


app = create_app()
