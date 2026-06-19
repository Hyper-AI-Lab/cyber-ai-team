"""FastAPI application entry point."""

import asyncio
import logging
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
    operations,
    roles,
    tools,
    workflows,
)
from cyber_team.api.security import get_current_principal
from cyber_team.audit.service import AuditService
from cyber_team.authorization.service import AuthorizationService
from cyber_team.clock import utc_now
from cyber_team.comms.email_triage import EmailTriageService
from cyber_team.comms.gateway import CommsGateway
from cyber_team.comms.inbound_email import InboundEmailService
from cyber_team.company.context_sync import CompanyContextSyncService
from cyber_team.config import settings
from cyber_team.db import async_session, init_db
from cyber_team.integrations.erpnext import ERPNextClient
from cyber_team.llm.gateway import LLMGateway
from cyber_team.memory.service import MemoryService
from cyber_team.observability.metrics import MetricsService
from cyber_team.operations.autonomous import AutonomousOperationsService
from cyber_team.operations.memory_steward import MemoryStewardService
from cyber_team.operations.owner_attention import OwnerAttentionNotificationService
from cyber_team.operations.planning import AutonomousPlanningService
from cyber_team.operations.retention import RetentionService
from cyber_team.operations.supervisor_review import SupervisorReviewService
from cyber_team.roles.loader import load_default_roles
from cyber_team.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings.validate_runtime_config()
    await init_db()
    app.state.metrics_service = MetricsService()
    app.state.audit_service = AuditService(metrics_service=app.state.metrics_service)
    await app.state.audit_service.record_control_evidence(
        control_id="runtime.config_validation",
        control_area="soc2_change_management",
        actor="system",
        outcome="success",
        evidence={
            "environment": settings.environment,
            "autonomy_side_effect_mode": settings.autonomy_side_effect_mode,
            "require_live_tool_executors": settings.require_live_tool_executors,
            "communications_allow_simulation": settings.communications_allow_simulation,
        },
    )
    app.state.authorization_service = AuthorizationService(
        audit_service=app.state.audit_service,
        metrics_service=app.state.metrics_service,
    )
    app.state.memory_service = MemoryService()
    app.state.retention_service = RetentionService(memory_service=app.state.memory_service)
    app.state.comms_gateway = CommsGateway(metrics_service=app.state.metrics_service)
    app.state.inbound_email_service = InboundEmailService(
        metrics_service=app.state.metrics_service,
        audit_service=app.state.audit_service,
    )
    app.state.llm_gateway = LLMGateway()
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
    app.state.supervisor_review_service = SupervisorReviewService(
        agent_manager=app.state.agent_manager,
        audit_service=app.state.audit_service,
    )
    app.state.memory_steward_service = MemoryStewardService(
        audit_service=app.state.audit_service,
        memory_service=app.state.memory_service,
        agent_manager=app.state.agent_manager,
    )
    app.state.autonomous_planning_service = AutonomousPlanningService(
        agent_manager=app.state.agent_manager,
        memory_steward_service=app.state.memory_steward_service,
        tool_registry=app.state.tool_registry,
        audit_service=app.state.audit_service,
    )
    app.state.company_context_sync_service = CompanyContextSyncService(
        erpnext=app.state.erpnext,
        agent_manager=app.state.agent_manager,
        memory_service=app.state.memory_service,
        tool_registry=app.state.tool_registry,
        audit_service=app.state.audit_service,
        planner=app.state.autonomous_planning_service,
    )
    app.state.autonomous_planning_service.set_company_context_service(
        app.state.company_context_sync_service
    )
    app.state.owner_attention_notification_service = OwnerAttentionNotificationService(
        planner=app.state.autonomous_planning_service,
        comms=app.state.comms_gateway,
        audit_service=app.state.audit_service,
        metrics_service=app.state.metrics_service,
    )
    app.state.autonomous_operations_service = AutonomousOperationsService(
        supervisor_review_service=app.state.supervisor_review_service,
        memory_steward_service=app.state.memory_steward_service,
        planning_service=app.state.autonomous_planning_service,
        audit_service=app.state.audit_service,
    )
    app.state.tool_registry.set_services(
        comms=app.state.comms_gateway,
        memory=app.state.memory_service,
        agent_manager=app.state.agent_manager,
        erpnext=app.state.erpnext,
        audit=app.state.audit_service,
        metrics=app.state.metrics_service,
    )
    app.state.email_triage_service = EmailTriageService(
        inbound_email_service=app.state.inbound_email_service,
        tool_registry=app.state.tool_registry,
        llm_gateway=app.state.llm_gateway,
        audit_service=app.state.audit_service,
        metrics_service=app.state.metrics_service,
    )
    await app.state.memory_service.startup()
    await load_default_roles()
    app.state.autonomous_operations_task = None
    app.state.supervisor_review_task = None
    app.state.memory_steward_task = None
    app.state.inbound_email_task = None
    app.state.company_context_drift_task = None
    app.state.operating_cadence_scheduler_task = None
    app.state.owner_attention_notification_task = None
    app.state.operating_cadence_scheduler_status = _initial_operating_cadence_scheduler_status()
    app.state.owner_attention_notification_status = (
        _initial_owner_attention_notification_status()
    )
    if settings.inbound_email_enabled:
        app.state.inbound_email_task = asyncio.create_task(_inbound_email_loop(app))
    if settings.erpnext_drift_detection_enabled and settings.erpnext_configured:
        app.state.company_context_drift_task = asyncio.create_task(
            _company_context_drift_loop(app)
        )
    if (
        settings.operating_cadence_scheduler_enabled
        and settings.autonomous_planner_enabled
    ):
        app.state.operating_cadence_scheduler_task = asyncio.create_task(
            _operating_cadence_scheduler_loop(app)
        )
    if settings.owner_attention_notifications_enabled:
        app.state.owner_attention_notification_task = asyncio.create_task(
            _owner_attention_notification_loop(app)
        )
    if settings.autonomous_operations_enabled:
        app.state.autonomous_operations_task = asyncio.create_task(
            _autonomous_operations_loop(app)
        )
    else:
        if settings.supervisor_review_enabled:
            app.state.supervisor_review_task = asyncio.create_task(
                _supervisor_review_loop(app)
            )
        if settings.memory_steward_enabled:
            app.state.memory_steward_task = asyncio.create_task(
                _memory_steward_loop(app)
            )
    try:
        yield
    finally:
        for task_name in (
            "autonomous_operations_task",
            "supervisor_review_task",
            "memory_steward_task",
            "inbound_email_task",
            "company_context_drift_task",
            "operating_cadence_scheduler_task",
            "owner_attention_notification_task",
        ):
            task = getattr(app.state, task_name, None)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    # Shutdown
    await app.state.memory_service.shutdown()
    await app.state.erpnext.close()


async def _autonomous_operations_loop(app: FastAPI) -> None:
    initial_delay = max(0, settings.autonomous_operations_initial_delay_seconds)
    interval = max(60, settings.autonomous_operations_interval_seconds)
    if initial_delay:
        await asyncio.sleep(initial_delay)
    while True:
        try:
            await app.state.autonomous_operations_service.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Autonomous operations loop failed")
        await asyncio.sleep(interval)


async def _supervisor_review_loop(app: FastAPI) -> None:
    initial_delay = max(0, settings.supervisor_review_initial_delay_seconds)
    interval = max(60, settings.supervisor_review_interval_seconds)
    if initial_delay:
        await asyncio.sleep(initial_delay)
    while True:
        try:
            await app.state.supervisor_review_service.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Supervisor review loop failed")
        await asyncio.sleep(interval)


async def _memory_steward_loop(app: FastAPI) -> None:
    initial_delay = max(0, settings.memory_steward_initial_delay_seconds)
    interval = max(60, settings.memory_steward_interval_seconds)
    if initial_delay:
        await asyncio.sleep(initial_delay)
    while True:
        try:
            await app.state.memory_steward_service.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Memory steward loop failed")
        await asyncio.sleep(interval)


async def _inbound_email_loop(app: FastAPI) -> None:
    await asyncio.sleep(10)
    interval = max(30, settings.inbound_email_poll_interval_seconds)
    while True:
        try:
            await app.state.inbound_email_service.poll_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Inbound email polling loop failed")
        await asyncio.sleep(interval)


async def _company_context_drift_loop(app: FastAPI) -> None:
    initial_delay = max(0, settings.erpnext_drift_initial_delay_seconds)
    interval = max(300, settings.erpnext_drift_interval_seconds)
    if initial_delay:
        await asyncio.sleep(initial_delay)
    while True:
        try:
            await app.state.company_context_sync_service.scan_for_erpnext_drift(
                actor="company_context_drift_scheduler",
                dry_run=False,
                apply_low_risk=settings.erpnext_drift_apply_low_risk,
                run_planner=settings.erpnext_drift_run_planner,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ERPNext company-context drift loop failed")
        await asyncio.sleep(interval)


def _initial_operating_cadence_scheduler_status() -> dict:
    enabled = (
        settings.operating_cadence_scheduler_enabled
        and settings.autonomous_planner_enabled
    )
    return {
        "enabled": enabled,
        "status": "idle" if enabled else "disabled",
        "detail": (
            "Scheduled operating cadence scans are waiting for the next run."
            if enabled
            else "Scheduled operating cadence scans are disabled."
        ),
        "actor": "operating_cadence_scheduler",
        "auto_execute": False,
        "interval_seconds": max(60, settings.operating_cadence_scheduler_interval_seconds),
        "limit": settings.operating_cadence_scheduler_limit,
        "last_started_at": None,
        "last_completed_at": None,
        "last_result": None,
        "last_error": None,
    }


def _initial_owner_attention_notification_status() -> dict:
    enabled = settings.owner_attention_notifications_enabled
    return {
        "enabled": enabled,
        "status": "idle" if enabled else "disabled",
        "detail": (
            "Owner attention notifications are waiting for the next run."
            if enabled
            else "Owner attention notifications are disabled."
        ),
        "actor": "owner_attention_notifier",
        "channel": "email",
        "interval_seconds": max(
            60,
            settings.owner_attention_notification_interval_seconds,
        ),
        "limit": settings.owner_attention_notification_limit,
        "last_started_at": None,
        "last_completed_at": None,
        "last_result": None,
        "last_error": None,
    }


async def _run_owner_attention_notification_once(
    app: FastAPI,
    *,
    actor: str = "owner_attention_notifier",
    dry_run: bool = False,
) -> dict:
    started_at = utc_now()
    status = {
        "enabled": settings.owner_attention_notifications_enabled,
        "status": "running",
        "detail": "Owner attention notification scan is running.",
        "actor": actor,
        "channel": "email",
        "interval_seconds": max(
            60,
            settings.owner_attention_notification_interval_seconds,
        ),
        "limit": settings.owner_attention_notification_limit,
        "last_started_at": started_at.isoformat(),
        "last_completed_at": None,
        "last_result": None,
        "last_error": None,
    }
    app.state.owner_attention_notification_status = status
    try:
        result = await app.state.owner_attention_notification_service.run_once(
            actor=actor,
            dry_run=dry_run,
            limit=settings.owner_attention_notification_limit,
        )
        completed_at = utc_now()
        final_status = {
            **status,
            "status": result.get("status") or "completed",
            "detail": result.get("detail") or "Owner attention notifications completed.",
            "last_completed_at": completed_at.isoformat(),
            "last_result": {
                "counts": result.get("counts", {}),
                "queue_counts": result.get("queue_counts", {}),
                "dry_run": result.get("dry_run", dry_run),
                "notification_status": result.get("notification_status", {}),
            },
            "last_error": None,
        }
        app.state.owner_attention_notification_status = final_status
        return final_status
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        completed_at = utc_now()
        final_status = {
            **status,
            "status": "failed",
            "detail": "Owner attention notification scan failed.",
            "last_completed_at": completed_at.isoformat(),
            "last_result": None,
            "last_error": str(exc),
        }
        app.state.owner_attention_notification_status = final_status
        audit_service = getattr(app.state, "audit_service", None)
        if audit_service:
            await audit_service.record(
                event_type="owner_attention.notification",
                actor=actor,
                actor_type="system",
                resource_type="owner_attention",
                resource_id=None,
                action="run",
                outcome="failure",
                metadata={"error": str(exc), "dry_run": dry_run},
            )
        logger.exception("Owner attention notification scan failed")
        return final_status


async def _run_operating_cadence_scheduler_once(app: FastAPI) -> dict:
    started_at = utc_now()
    actor = "operating_cadence_scheduler"
    auto_execute = (
        settings.operating_cadence_scheduler_auto_execute
        and settings.autonomy_side_effect_mode != "manual_only"
    )
    status = {
        "enabled": True,
        "status": "running",
        "detail": "Scheduled operating cadence scan is running.",
        "actor": actor,
        "auto_execute": auto_execute,
        "interval_seconds": max(60, settings.operating_cadence_scheduler_interval_seconds),
        "limit": settings.operating_cadence_scheduler_limit,
        "last_started_at": started_at.isoformat(),
        "last_completed_at": None,
        "last_result": None,
        "last_error": None,
    }
    app.state.operating_cadence_scheduler_status = status
    try:
        result = await app.state.autonomous_planning_service.scan_operating_cadences(
            actor=actor,
            auto_execute=auto_execute,
            limit=settings.operating_cadence_scheduler_limit,
        )
        completed_at = utc_now()
        errors = result.get("errors") or []
        compact_result = {
            "scanned_at": result.get("scanned_at"),
            "cadences_reviewed": result.get("cadences_reviewed", 0),
            "cadences_due": result.get("cadences_due", 0),
            "plans_created": result.get("plans_created", 0),
            "plans_existing": result.get("plans_existing", 0),
            "created_plan_ids": result.get("created_plan_ids", []),
            "existing_plan_ids": result.get("existing_plan_ids", []),
            "errors": errors,
            "execution": result.get("execution"),
        }
        final_status = {
            **status,
            "status": "degraded" if errors else "completed",
            "detail": (
                "Scheduled operating cadence scan completed with errors."
                if errors
                else "Scheduled operating cadence scan completed."
            ),
            "last_completed_at": completed_at.isoformat(),
            "last_result": compact_result,
            "last_error": None,
        }
        app.state.operating_cadence_scheduler_status = final_status
        audit_service = getattr(app.state, "audit_service", None)
        if audit_service:
            await audit_service.record(
                event_type="operating_cadence.scheduler_run",
                actor=actor,
                actor_type="system",
                resource_type="operating_cadence",
                resource_id=None,
                action="scan",
                outcome="degraded" if errors else "success",
                metadata={
                    "auto_execute": auto_execute,
                    "cadences_reviewed": compact_result["cadences_reviewed"],
                    "cadences_due": compact_result["cadences_due"],
                    "plans_created": compact_result["plans_created"],
                    "plans_existing": compact_result["plans_existing"],
                    "errors": errors,
                },
            )
        return final_status
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        completed_at = utc_now()
        final_status = {
            **status,
            "status": "failed",
            "detail": "Scheduled operating cadence scan failed.",
            "last_completed_at": completed_at.isoformat(),
            "last_result": None,
            "last_error": str(exc),
        }
        app.state.operating_cadence_scheduler_status = final_status
        audit_service = getattr(app.state, "audit_service", None)
        if audit_service:
            await audit_service.record(
                event_type="operating_cadence.scheduler_run",
                actor=actor,
                actor_type="system",
                resource_type="operating_cadence",
                resource_id=None,
                action="scan",
                outcome="failure",
                metadata={
                    "auto_execute": auto_execute,
                    "error": str(exc),
                },
            )
        logger.exception("Scheduled operating cadence scan failed")
        return final_status


async def _operating_cadence_scheduler_loop(app: FastAPI) -> None:
    initial_delay = max(0, settings.operating_cadence_scheduler_initial_delay_seconds)
    interval = max(60, settings.operating_cadence_scheduler_interval_seconds)
    if initial_delay:
        await asyncio.sleep(initial_delay)
    while True:
        await _run_operating_cadence_scheduler_once(app)
        await asyncio.sleep(interval)


async def _owner_attention_notification_loop(app: FastAPI) -> None:
    initial_delay = max(0, settings.owner_attention_notification_initial_delay_seconds)
    interval = max(60, settings.owner_attention_notification_interval_seconds)
    if initial_delay:
        await asyncio.sleep(initial_delay)
    while True:
        await _run_owner_attention_notification_once(app)
        await asyncio.sleep(interval)


app = FastAPI(
    title=settings.app_name,
    description="AI-powered digital company operating system",
    version=settings.app_version,
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
    operations.router,
    prefix="/api/operations",
    tags=["operations"],
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
    return {
        "status": "ok",
        "version": settings.app_version,
        "build_sha": settings.build_sha,
        "environment": settings.environment,
    }


@app.get("/live")
async def live():
    return {
        "status": "ok",
        "version": settings.app_version,
        "build_sha": settings.build_sha,
        "environment": settings.environment,
    }


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
            "version": settings.app_version,
            "build_sha": settings.build_sha,
            "environment": settings.environment,
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
