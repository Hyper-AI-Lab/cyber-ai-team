"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from cyber_team.config import settings
from cyber_team.api.routes import agents, workflows, memory, roles, chat, comms, dashboard, auth, tools, audit
from cyber_team.db import init_db
from cyber_team.memory.service import MemoryService
from cyber_team.agents.manager import AgentManager
from cyber_team.agents.orchestrator import Orchestrator
from cyber_team.comms.gateway import CommsGateway
from cyber_team.integrations.erpnext import ERPNextClient
from cyber_team.roles.loader import load_default_roles
from cyber_team.tools.registry import ToolRegistry
from cyber_team.api.security import require_owner
from cyber_team.audit.service import AuditService


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings.validate_runtime_config()
    await init_db()
    app.state.audit_service = AuditService()
    app.state.memory_service = MemoryService()
    app.state.comms_gateway = CommsGateway()
    app.state.erpnext = ERPNextClient()
    app.state.tool_registry = ToolRegistry()
    app.state.agent_manager = AgentManager(
        memory_service=app.state.memory_service,
        audit_service=app.state.audit_service,
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

# Register routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
protected_dependencies = [Depends(require_owner)]
app.include_router(agents.router, prefix="/api/agents", tags=["agents"], dependencies=protected_dependencies)
app.include_router(workflows.router, prefix="/api/workflows", tags=["workflows"], dependencies=protected_dependencies)
app.include_router(memory.router, prefix="/api/memory", tags=["memory"], dependencies=protected_dependencies)
app.include_router(roles.router, prefix="/api/roles", tags=["roles"], dependencies=protected_dependencies)
app.include_router(chat.router, prefix="/api/chat", tags=["chat"], dependencies=protected_dependencies)
app.include_router(comms.router, prefix="/api/comms", tags=["communications"], dependencies=protected_dependencies)
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"], dependencies=protected_dependencies)
app.include_router(tools.router, prefix="/api/tools", tags=["tools"], dependencies=protected_dependencies)
app.include_router(audit.router, prefix="/api/audit", tags=["audit"], dependencies=protected_dependencies)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/metrics")
async def metrics():
    return Response("cyberteam_up 1\n", media_type="text/plain; version=0.0.4")
