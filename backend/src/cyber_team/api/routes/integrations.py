"""Integration status routes."""

import asyncio
from collections.abc import Awaitable

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal
from cyber_team.clock import utc_now
from cyber_team.config import settings

router = APIRouter()


class IntegrationValidationRequest(BaseModel):
    provider: str = Field(default="smtp", min_length=1, max_length=64)


async def _bounded_status_validation(
    provider: str,
    validation: Awaitable[dict],
    *,
    configured: bool,
) -> dict:
    timeout = max(0.1, settings.integration_status_validation_timeout_seconds)
    try:
        return await asyncio.wait_for(validation, timeout=timeout)
    except TimeoutError:
        return {
            "provider": provider,
            "configured": configured,
            "mode": "validation_timeout",
            "status": "validation_timeout",
            "blocking": True,
            "detail": (
                f"{provider} status validation exceeded the bounded "
                f"{timeout:g}-second deadline."
            ),
            "checked_at": utc_now().isoformat() + "+00:00",
        }
    except Exception as exc:  # noqa: BLE001 - status reads must fail closed.
        return {
            "provider": provider,
            "configured": configured,
            "mode": "validation_failed",
            "status": "validation_failed",
            "blocking": True,
            "detail": f"{provider} status validation failed: {type(exc).__name__}.",
            "checked_at": utc_now().isoformat() + "+00:00",
        }


def _required_provider_names() -> set[str]:
    return settings.required_provider_names


def _provider_key(item: dict) -> str:
    return str(item.get("provider") or item.get("channel") or "").lower()


def _annotate_provider_status(item: dict) -> dict:
    annotated = dict(item)
    provider = _provider_key(annotated)
    required = provider in _required_provider_names()
    mode = annotated.get("mode")
    live = mode == "live"
    optional_disabled = not required and not live
    blocking = settings.require_live_tool_executors and required and not live
    annotated.update(
        {
            "required": required,
            "optional_disabled": optional_disabled,
            "blocking": blocking,
        }
    )
    return annotated


def _blocking_reasons(items: list[dict]) -> list[dict]:
    return [
        {
            "channel": item.get("channel"),
            "provider": item.get("provider"),
            "mode": item.get("mode"),
            "required": item.get("required"),
            "reason": item.get("detail") or item.get("readiness_reason"),
        }
        for item in items
        if item.get("blocking")
    ]


@router.get("/status")
async def integration_status(
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(request, principal, "read", "integration_status")
    comms = request.app.state.comms_gateway
    communications = [_annotate_provider_status(item) for item in comms.integration_status()]
    inbound_email = getattr(request.app.state, "inbound_email_service", None)
    if inbound_email:
        communications = [
            *communications,
            _annotate_provider_status(inbound_email.integration_status()),
        ]
    erpnext = getattr(request.app.state, "erpnext", None)
    erpnext_last_validation = getattr(
        request.app.state,
        "erpnext_last_validation_result",
        None,
    )
    llm_gateway = getattr(request.app.state, "llm_gateway", None)
    validations: list[tuple[str, Awaitable[dict], bool]] = []
    if erpnext and hasattr(erpnext, "validate"):
        validations.append(("erpnext", erpnext.validate(), bool(erpnext.configured)))
    if llm_gateway and hasattr(llm_gateway, "validate_provider"):
        llm_configured = bool(
            settings.llm_effective_api_keys or settings.llm_local_fallback_enabled
        )
        validations.append(("llm", llm_gateway.validate_provider(), llm_configured))
    validation_results = dict(
        zip(
            (provider for provider, _, _ in validations),
            await asyncio.gather(
                *(
                    _bounded_status_validation(
                        provider,
                        validation,
                        configured=configured,
                    )
                    for provider, validation, configured in validations
                )
            ),
            strict=True,
        )
    )
    erpnext_status = None
    if erpnext:
        if "erpnext" in validation_results:
            erpnext_last_validation = validation_results["erpnext"]
            request.app.state.erpnext_last_validation_result = erpnext_last_validation
        erpnext_status = _annotate_provider_status(
            erpnext.integration_status(erpnext_last_validation)
        )
        company_context_service = getattr(
            request.app.state,
            "company_context_sync_service",
            None,
        )
        if company_context_service:
            latest_snapshot = await company_context_service.latest_snapshot()
            latest_runs = await company_context_service.list_sync_runs(limit=1)
            erpnext_status["company_context"] = (
                company_context_service.readiness_from_snapshot(
                    latest_snapshot,
                    latest_run=latest_runs[0] if latest_runs else None,
                )
            )
    elif "erpnext" in _required_provider_names():
        erpnext_status = _annotate_provider_status(
            {
                "provider": "erpnext",
                "configured": False,
                "mode": "configuration_required",
                "detail": "ERPNext client is not available.",
            }
        )
    provider_items = [*communications]
    if erpnext_status:
        provider_items.append(erpnext_status)
    if llm_gateway and hasattr(llm_gateway, "validate_provider"):
        llm_status = validation_results["llm"]
        provider_items.append(
            {
                **llm_status,
                "required": True,
                "optional_disabled": False,
                "blocking": llm_status.get("mode") != "live",
            }
        )
    else:
        llm_status = {
            "provider": settings.llm_provider_name,
            "configured": False,
            "mode": "configuration_required",
            "status": "configuration_required",
            "detail": "LLM gateway is not available.",
            "required": True,
            "optional_disabled": False,
            "blocking": True,
        }
        provider_items.append(llm_status)
    blocking_reasons = _blocking_reasons(provider_items)
    last_validation = comms.last_validation_result()
    if inbound_email and inbound_email.last_validation_result():
        last_validation = inbound_email.last_validation_result()
    if erpnext_last_validation:
        last_validation = erpnext_last_validation
    return {
        "environment": settings.environment,
        "communications": communications,
        "erpnext": erpnext_status,
        "llm": llm_status,
        "required_providers": sorted(_required_provider_names()),
        "optional_disabled": [
            item for item in provider_items if item.get("optional_disabled")
        ],
        "simulation_enabled": settings.communications_allow_simulation,
        "require_live_tool_executors": settings.require_live_tool_executors,
        "production_blocking_readiness": bool(blocking_reasons),
        "blocking_reasons": blocking_reasons,
        "last_validation_result": last_validation or {
            "status": "blocked" if blocking_reasons else "ready",
            "checked_at": None,
            "provider": "all",
            "results": [],
        },
    }


@router.post("/validate")
async def validate_integration(
    data: IntegrationValidationRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "validate",
        "integration",
        data.provider,
        context=data.model_dump(),
    )
    inbound_email = getattr(request.app.state, "inbound_email_service", None)
    provider = data.provider.lower()
    if provider == "erpnext":
        erpnext = getattr(request.app.state, "erpnext", None)
        if not erpnext:
            result = {
                "status": "failed",
                "checked_at": utc_now().isoformat() + "+00:00",
                "provider": "erpnext",
                "results": [
                    {
                        "provider": "erpnext",
                        "status": "failed",
                        "mode": "unavailable",
                        "detail": "ERPNext client is not available.",
                    }
                ],
            }
        else:
            validation = await erpnext.validate(force=True)
            checked_at = utc_now().isoformat() + "+00:00"
            result_item = dict(validation)
            result_item.pop("results", None)
            result = {
                **validation,
                "checked_at": checked_at,
                "provider": "erpnext",
                "results": [result_item],
            }
        request.app.state.erpnext_last_validation_result = result
    elif provider in {"mistral", "mistral_llm", "openai", "openai_llm", "llm"}:
        llm_gateway = getattr(request.app.state, "llm_gateway", None)
        if llm_gateway and hasattr(llm_gateway, "validate_provider"):
            validation = await llm_gateway.validate_provider(force=True)
        else:
            validation = {
                "provider": settings.llm_provider_name,
                "configured": False,
                "mode": "configuration_required",
                "status": "configuration_required",
                "detail": "LLM gateway is not available.",
            }
        checked_at = utc_now().isoformat() + "+00:00"
        result = {
            "status": "ready" if validation.get("mode") == "live" else "failed",
            "checked_at": checked_at,
            "provider": str(validation.get("provider") or settings.llm_provider_name),
            "results": [
                {
                    **validation,
                    "provider": str(
                        validation.get("provider") or settings.llm_provider_name
                    ),
                    "status": validation.get("status") or validation.get("mode"),
                }
            ],
        }
    elif inbound_email and provider in {"imap", "inbound_email"}:
        result = await inbound_email.validate()
    else:
        result = await request.app.state.comms_gateway.validate_integrations(data.provider)
    audit = getattr(request.app.state, "audit_service", None)
    if audit:
        await audit.record_control_evidence(
            control_id="integration.validation",
            control_area="soc2_availability",
            actor=principal.email,
            outcome=result["status"],
            evidence={
                "environment": settings.environment,
                "provider": result["provider"],
                "status": result["status"],
                "checked_at": result["checked_at"],
                "results": result["results"],
            },
        )
    return result
