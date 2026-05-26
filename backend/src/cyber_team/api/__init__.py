"""FastAPI application entry point."""

import asyncio
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text
from starlette.requests import Request
from starlette.responses import JSONResponse
from temporalio.client import Client

from cyber_team.agents.manager import AgentManager
from cyber_team.agents.orchestrator import Orchestrator
from cyber_team.api.routes import (
    agents,
    audit,
    auth,
    chat,
    comms,
    dashboard,
    integrations,
    memory,
    roles,
    tools,
    workflows,
)
from cyber_team.api.security import get_current_principal
from cyber_team.audit.service import AuditService
from cyber_team.authorization.service import AuthorizationService
from cyber_team.comms.gateway import CommsGateway
from cyber_team.config import settings
from cyber_team.db import async_session, init_db
from cyber_team.integrations.erpnext import ERPNextClient
from cyber_team.memory.service import MemoryService
from cyber_team.observability.metrics import MetricsService
from cyber_team.roles.loader import load_default_roles
from cyber_team.tools.registry import ToolRegistry


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings.validate_runtime_config()
    await init_db()
    app.state.metrics_service = MetricsService()
    app.state.audit_service = AuditService(metrics_service=app.state.metrics_service)
    app.state.authorization_service = AuthorizationService(
        audit_service=app.state.audit_service,
        metrics_service=app.state.metrics_service,
    )
    app.state.memory_service = MemoryService()
    app.state.comms_gateway = CommsGateway()
    app.state.erpnext = ERPNextClient()
    app.state.tool_registry = ToolRegistry()
    app.state.agent_manager = AgentManager(
        memory_service=app.state.memory_service,
        audit_service=app.state.audit_service,
        tool_registry=app.state.tool_registry,
    )
    app.state.orchestrator = Orchestrator(
        agent_manager=app.state.agent_manager,
        memory_service=app.state.memory_service,
        tool_registry=app.state.tool_registry,
    )
    app.state.tool_registry.set_services(
        comms=app.state.comms_gateway,
        memory=app.state.memory_service,
        agent_manager=app.state.agent_manager,
        erpnext=app.state.erpnext,
        audit=app.state.audit_service,
    )
    await app.state.memory_service.startup()
    await load_default_roles()
    yield
    # Shutdown
    await app.state.memory_service.shutdown()
    await app.state.erpnext.close()


app = FastAPI(
    title=settings.app_name,
    description="AI-powered digital company operating system",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=not settings.cors_allows_wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next: Callable):
    started_at = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        metrics_service = getattr(request.app.state, "metrics_service", None)
        if metrics_service:
            metrics_service.record_http_request(
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_seconds=time.perf_counter() - started_at,
            )


# Register routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
protected_dependencies = [Depends(get_current_principal)]
app.include_router(
    agents.router,
    prefix="/api/agents",
    tags=["agents"],
    dependencies=protected_dependencies,
)
app.include_router(
    workflows.router,
    prefix="/api/workflows",
    tags=["workflows"],
    dependencies=protected_dependencies,
)
app.include_router(
    memory.router,
    prefix="/api/memory",
    tags=["memory"],
    dependencies=protected_dependencies,
)
app.include_router(
    roles.router,
    prefix="/api/roles",
    tags=["roles"],
    dependencies=protected_dependencies,
)
app.include_router(
    chat.router,
    prefix="/api/chat",
    tags=["chat"],
    dependencies=protected_dependencies,
)
app.include_router(
    comms.router,
    prefix="/api/comms",
    tags=["communications"],
    dependencies=protected_dependencies,
)
app.include_router(
    dashboard.router,
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=protected_dependencies,
)
app.include_router(
    tools.router,
    prefix="/api/tools",
    tags=["tools"],
    dependencies=protected_dependencies,
)
app.include_router(
    audit.router,
    prefix="/api/audit",
    tags=["audit"],
    dependencies=protected_dependencies,
)
app.include_router(
    integrations.router,
    prefix="/api/integrations",
    tags=["integrations"],
    dependencies=protected_dependencies,
)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/live")
async def live():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/ready")
async def ready():
    checks = [
        await _readiness_check("postgres", _check_postgres),
        await _readiness_check("redis", _check_redis),
        await _readiness_check("qdrant", _check_qdrant),
        await _readiness_check("temporal", _check_temporal),
        await _readiness_check("opa", _check_opa),
    ]
    ready_status = all(check["status"] == "ok" for check in checks)
    return JSONResponse(
        status_code=200 if ready_status else 503,
        content={
            "status": "ready" if ready_status else "degraded",
            "version": "0.1.0",
            "checks": checks,
        },
    )


@app.get("/metrics")
async def metrics():
    return Response(
        app.state.metrics_service.render_prometheus(),
        media_type="text/plain; version=0.0.4",
    )


async def _readiness_check(name: str, checker: Callable[[], object]) -> dict:
    try:
        await checker()
        return {"name": name, "status": "ok"}
    except Exception as exc:
        return {"name": name, "status": "failed", "error": str(exc)}


async def _check_postgres() -> None:
    async with async_session() as session:
        await session.execute(text("SELECT 1"))


async def _check_redis() -> None:
    client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _check_qdrant() -> None:
    async with httpx.AsyncClient(timeout=2.0) as client:
        response = await client.get(f"{settings.qdrant_url}/healthz")
        response.raise_for_status()


async def _check_temporal() -> None:
    await asyncio.wait_for(
        Client.connect(settings.temporal_url, namespace=settings.temporal_namespace),
        timeout=2.0,
    )


async def _check_opa() -> None:
    async with httpx.AsyncClient(timeout=2.0) as client:
        response = await client.get(f"{settings.opa_api_url}/health")
        response.raise_for_status()
