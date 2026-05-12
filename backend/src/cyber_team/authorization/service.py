from typing import Any, Optional

import httpx
from pydantic import BaseModel

from cyber_team.api.security import Principal
from cyber_team.audit.service import AuditService
from cyber_team.config import settings
from cyber_team.observability.metrics import MetricsService


class AuthorizationDecision(BaseModel):
    allowed: bool
    reason: str
    source: str = "local"
    policy: Optional[str] = None


class AuthorizationService:
    def __init__(
        self,
        audit_service: Optional[AuditService] = None,
        metrics_service: Optional[MetricsService] = None,
    ):
        self._audit = audit_service
        self._metrics = metrics_service

    async def authorize(
        self,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
        audit: bool = True,
    ) -> AuthorizationDecision:
        context = context or {}
        decision = await self._opa_decision(principal, action, resource_type, resource_id, context)
        if decision is None:
            decision = self._local_decision(principal, action, resource_type, resource_id, context)
        if audit and self._audit:
            actor_type = "agent" if principal.role == "agent" else "user"
            await self._audit.record(
                event_type="authorization.allowed" if decision.allowed else "authorization.denied",
                actor=principal.email,
                actor_type=actor_type,
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                outcome="success" if decision.allowed else "denied",
                metadata={
                    "subject": principal.subject,
                    "role": principal.role,
                    "reason": decision.reason,
                    "source": decision.source,
                    "policy": decision.policy,
                    "context": self._safe_context(context),
                },
            )
        if self._metrics:
            self._metrics.record_authorization_decision(
                allowed=decision.allowed,
                resource_type=resource_type,
                action=action,
                source=decision.source,
            )
        return decision

    async def _opa_decision(
        self,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: Optional[str],
        context: dict[str, Any],
    ) -> Optional[AuthorizationDecision]:
        payload = {
            "input": {
                **context,
                "subject": principal.subject,
                "email": principal.email,
                "role": principal.role,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
            }
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{settings.opa_api_url}/v1/data/cyberteam/authz/allow",
                    json=payload,
                    timeout=2.0,
                )
                if response.status_code != 200:
                    return None
                result = response.json().get("result")
        except Exception:
            return None
        if result is None:
            return None
        return AuthorizationDecision(
            allowed=bool(result),
            reason="opa_policy_allowed" if result else "opa_policy_denied",
            source="opa",
            policy="cyberteam.authz.allow",
        )

    def _local_decision(
        self,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: Optional[str],
        context: dict[str, Any],
    ) -> AuthorizationDecision:
        if principal.role == "owner":
            return AuthorizationDecision(allowed=True, reason="owner_role_allows_all")
        if (
            principal.role == "agent"
            and resource_type == "tool"
            and action in {"read", "execute"}
        ):
            allowed_tools = context.get("allowed_tools") or []
            if action == "read" and resource_id is None:
                return AuthorizationDecision(allowed=True, reason="agent_tool_catalog")
            if resource_id and resource_id in allowed_tools:
                return AuthorizationDecision(allowed=True, reason="agent_tool_assignment")
        if (
            principal.role == "agent"
            and resource_type == "memory_namespace"
            and action in {"read", "write"}
        ):
            if resource_id and resource_id == context.get("memory_namespace"):
                return AuthorizationDecision(
                    allowed=True,
                    reason="agent_memory_namespace_assignment",
                )
        return AuthorizationDecision(allowed=False, reason="no_matching_policy")

    @staticmethod
    def _safe_context(context: dict[str, Any]) -> dict[str, Any]:
        blocked = {"password", "token", "secret", "api_key", "api_secret"}
        return {
            key: value
            for key, value in context.items()
            if not any(blocked_key in key.lower() for blocked_key in blocked)
        }
