"""Scheduled owner executive brief delivery."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from cyber_team.clock import utc_now
from cyber_team.config import settings


@dataclass(frozen=True)
class ExecutiveBriefEmail:
    to_address: str
    subject: str
    body: str
    agent_id: str | None = "chief-operating-agent"
    cc: list[str] | None = None
    idempotency_key: str | None = None


class ExecutiveBriefEmailService:
    """Send deduplicated owner-facing executive operating briefs."""

    EVENT_TYPE = "executive_brief.email"
    RESOURCE_TYPE = "executive_brief"

    def __init__(
        self,
        *,
        executive_service,
        comms,
        audit_service,
        metrics_service=None,
    ) -> None:
        self._executive = executive_service
        self._comms = comms
        self._audit = audit_service
        self._metrics = metrics_service

    async def run_once(
        self,
        *,
        actor: str = "executive_brief_email_scheduler",
        dry_run: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        started_at = utc_now()
        config_status = await self.status()
        if not settings.executive_brief_email_enabled:
            return self._empty_result(
                started_at,
                actor=actor,
                status="disabled",
                detail="Executive brief email delivery is disabled.",
                config_status=config_status,
                dry_run=dry_run,
                force=force,
            )
        if config_status["status"] not in {"ready", "simulated"}:
            await self._record_event(
                actor=actor,
                action="configuration_check",
                outcome="skipped",
                metadata={
                    "reason": config_status["detail"],
                    "brief_email_status": config_status,
                    "dry_run": dry_run,
                    "force": force,
                },
            )
            return self._empty_result(
                started_at,
                actor=actor,
                status="configuration_required",
                detail=config_status["detail"],
                config_status=config_status,
                dry_run=dry_run,
                force=force,
            )

        if not force and await self._recently_sent():
            await self._record_event(
                actor=actor,
                action="skip",
                outcome="skipped",
                metadata={
                    "reason": "recently_sent",
                    "cooldown_hours": settings.executive_brief_email_cooldown_hours,
                    "dry_run": dry_run,
                    "force": force,
                },
            )
            return self._empty_result(
                started_at,
                actor=actor,
                status="skipped",
                detail="Executive brief email was sent recently; cooldown is active.",
                config_status=config_status,
                dry_run=dry_run,
                force=force,
            )

        brief = await self._executive.executive_brief()
        email = self._email_for_brief(brief, started_at, force=force)
        if dry_run:
            event = await self._record_event(
                actor=actor,
                action="dry_run",
                outcome="skipped",
                metadata={
                    "reason": "dry_run",
                    "recipient": settings.owner_email,
                    "subject": email.subject,
                    "idempotency_key": email.idempotency_key,
                    "brief_summary": self._brief_summary(brief),
                    "force": force,
                },
            )
            return self._result(
                started_at,
                actor=actor,
                status="dry_run",
                detail="Executive brief email dry run completed.",
                config_status=config_status,
                dry_run=dry_run,
                force=force,
                response=None,
                event=event,
                brief=brief,
            )

        response = await self._comms.send_email(email)
        response_status = response.get("status") or "unknown"
        if response_status in {"sent", "simulated"}:
            outcome = response_status
            status = "ready" if response_status == "sent" else "simulated"
            detail = (
                "Executive brief email was sent."
                if response_status == "sent"
                else "Executive brief email was simulated."
            )
        else:
            outcome = "failed"
            status = "degraded"
            detail = "Executive brief email delivery failed."
        event = await self._record_event(
            actor=actor,
            action="send_email",
            outcome=outcome,
            metadata={
                "recipient": settings.owner_email,
                "subject": email.subject,
                "idempotency_key": email.idempotency_key,
                "response": self._redact_response(response),
                "brief_summary": self._brief_summary(brief),
                "force": force,
            },
        )
        self._record_metric(outcome)
        return self._result(
            started_at,
            actor=actor,
            status=status,
            detail=detail,
            config_status=config_status,
            dry_run=dry_run,
            force=force,
            response=response,
            event=event,
            brief=brief,
        )

    async def status(self) -> dict[str, Any]:
        email_status = self._email_provider_status()
        if not settings.executive_brief_email_enabled:
            status = "disabled"
            detail = "Executive brief email delivery is disabled."
        elif not settings.owner_email:
            status = "configuration_required"
            detail = "OWNER_EMAIL is required for executive brief email delivery."
        elif email_status.get("mode") == "live":
            status = "ready"
            detail = "Executive brief email delivery can send live owner email."
        elif email_status.get("mode") == "simulated":
            status = "simulated"
            detail = "Executive brief email delivery will be simulated because SMTP is not live."
        else:
            status = "configuration_required"
            detail = "Live SMTP or communications simulation is required for executive briefs."

        recent_events = []
        last_event = None
        if self._audit:
            recent_events = await self._audit.list_events(
                event_type=self.EVENT_TYPE,
                limit=20,
            )
            last_event = recent_events[0] if recent_events else None
        return {
            "enabled": settings.executive_brief_email_enabled,
            "status": status,
            "detail": detail,
            "channel": "email",
            "recipient": settings.owner_email,
            "owner_console_url": settings.owner_console_url,
            "interval_seconds": max(
                3600,
                settings.executive_brief_email_interval_seconds,
            ),
            "cooldown_hours": settings.executive_brief_email_cooldown_hours,
            "email_provider": email_status,
            "last_event": last_event,
            "recent_counts": self._event_counts(recent_events),
        }

    def _email_provider_status(self) -> dict[str, Any]:
        for item in self._comms.integration_status():
            if item.get("channel") == "email" and item.get("provider") == "smtp":
                return item
        return {
            "channel": "email",
            "provider": "smtp",
            "configured": False,
            "mode": "disabled",
            "detail": "SMTP provider status is unavailable.",
        }

    async def _recently_sent(self) -> bool:
        if not self._audit:
            return False
        events = await self._audit.list_events(
            event_type=self.EVENT_TYPE,
            limit=50,
        )
        cutoff = utc_now() - timedelta(
            hours=max(1, settings.executive_brief_email_cooldown_hours)
        )
        for event in events:
            if event.get("outcome") not in {"sent", "simulated"}:
                continue
            created_at = self._parse_datetime(event.get("created_at"))
            if created_at and created_at >= cutoff:
                return True
        return False

    def _email_for_brief(
        self,
        brief: dict[str, Any],
        generated_at: datetime,
        *,
        force: bool,
    ) -> ExecutiveBriefEmail:
        date_key = generated_at.strftime("%Y-%m-%d")
        idempotency_key = f"executive-brief:{date_key}"
        if force:
            idempotency_key = f"{idempotency_key}:force:{uuid.uuid4().hex[:12]}"
        return ExecutiveBriefEmail(
            to_address=settings.owner_email,
            subject=f"Cyber-Team Executive Brief - {date_key}",
            body=self._body_for_brief(brief, generated_at),
            cc=[],
            idempotency_key=idempotency_key,
        )

    def _body_for_brief(self, brief: dict[str, Any], generated_at: datetime) -> str:
        latest = brief.get("latest_run") or {}
        readiness = brief.get("readiness") or {}
        observer = brief.get("observer") or {}
        objectives = (brief.get("objectives") or {}).get("items") or []
        kpis = (brief.get("kpis") or {}).get("items") or []
        benchmark_results = (
            ((brief.get("benchmarks") or {}).get("latest_results") or {}).get("items")
            or []
        )
        outsourcing = brief.get("outsourcing") or {}
        resource_policy = brief.get("resource_policy") or {}
        blocked_actions = latest.get("blocked_actions") or []
        approvals = latest.get("approvals_created") or []
        sections = [
            "Cyber-Team Executive Brief",
            f"Generated: {generated_at.isoformat()}",
            f"Owner console: {settings.owner_console_url}",
            "",
            "Operating State",
            f"- Readiness: {readiness.get('status', 'unknown')}",
            (
                f"- Latest executive run: {latest.get('status', 'not_run')} "
                f"({latest.get('run_id', '-')})"
            ),
            f"- Observer: {observer.get('status', 'unknown')}",
            f"- Resource policy: {resource_policy.get('status', 'unknown')}",
            f"- Blocked/gated actions: {len(blocked_actions)}",
            f"- Approvals created: {len(approvals)}",
            f"- Open outsourcing requests: {outsourcing.get('count', 0)}",
            "",
            "Objectives",
            *self._format_items(
                objectives,
                lambda item: (
                    f"- {item.get('title', 'Untitled objective')} "
                    f"[{item.get('priority', 'normal')}/{item.get('status', 'unknown')}]"
                ),
            ),
            "",
            "KPIs",
            *self._format_items(
                kpis,
                lambda item: (
                    f"- {item.get('key') or item.get('name') or item.get('id', 'kpi')}: "
                    f"{item.get('value', item.get('status', 'recorded'))}"
                ),
            ),
            "",
            "Benchmarks",
            *self._format_items(
                benchmark_results,
                lambda item: (
                    f"- {item.get('benchmark_key', 'benchmark')}: "
                    f"{item.get('status', 'unknown')}"
                ),
            ),
            "",
            "Watch Items",
            *self._format_items(
                (latest.get("reflection_summary") or {}).get("next_watch_items") or [],
                lambda item: f"- {item}",
            ),
        ]
        if blocked_actions:
            sections.extend(
                [
                    "",
                    "Blocked Or Gated Actions",
                    *self._format_items(
                        blocked_actions,
                        lambda item: (
                            f"- {item.get('title', item.get('action_type', 'action'))}: "
                            f"{item.get('status', 'blocked')}"
                        ),
                    ),
                ]
            )
        return "\n".join(sections).strip() + "\n"

    @staticmethod
    def _format_items(items: list[Any], formatter) -> list[str]:
        if not items:
            return ["- None recorded."]
        return [formatter(item) for item in items[:8]]

    @staticmethod
    def _brief_summary(brief: dict[str, Any]) -> dict[str, Any]:
        latest = brief.get("latest_run") or {}
        return {
            "latest_run_id": latest.get("run_id"),
            "latest_run_status": latest.get("status"),
            "blocked_actions": len(latest.get("blocked_actions") or []),
            "approvals_created": len(latest.get("approvals_created") or []),
            "objectives": (brief.get("objectives") or {}).get("count", 0),
            "kpis": (brief.get("kpis") or {}).get("count", 0),
            "benchmark_results": (
                ((brief.get("benchmarks") or {}).get("latest_results") or {}).get(
                    "count",
                    0,
                )
            ),
            "outsourcing_open": (brief.get("outsourcing") or {}).get("count", 0),
        }

    async def _record_event(
        self,
        *,
        actor: str,
        action: str,
        outcome: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not self._audit:
            return None
        return await self._audit.record(
            event_type=self.EVENT_TYPE,
            actor=actor,
            actor_type="system",
            resource_type=self.RESOURCE_TYPE,
            resource_id="daily",
            action=action,
            outcome=outcome,
            metadata=metadata,
        )

    def _empty_result(
        self,
        started_at: datetime,
        *,
        actor: str,
        status: str,
        detail: str,
        config_status: dict[str, Any],
        dry_run: bool,
        force: bool,
    ) -> dict[str, Any]:
        return self._result(
            started_at,
            actor=actor,
            status=status,
            detail=detail,
            config_status=config_status,
            dry_run=dry_run,
            force=force,
            response=None,
            event=None,
            brief=None,
        )

    def _result(
        self,
        started_at: datetime,
        *,
        actor: str,
        status: str,
        detail: str,
        config_status: dict[str, Any],
        dry_run: bool,
        force: bool,
        response: dict[str, Any] | None,
        event: dict[str, Any] | None,
        brief: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "enabled": settings.executive_brief_email_enabled,
            "status": status,
            "detail": detail,
            "actor": actor,
            "dry_run": dry_run,
            "force": force,
            "started_at": started_at.isoformat(),
            "completed_at": utc_now().isoformat(),
            "notification_status": config_status,
            "response": self._redact_response(response) if response else None,
            "event_id": event.get("id") if event else None,
            "brief_summary": self._brief_summary(brief) if brief else None,
        }

    @staticmethod
    def _redact_response(response: dict[str, Any] | None) -> dict[str, Any] | None:
        if not response:
            return None
        return {
            key: value
            for key, value in response.items()
            if key not in {"provider_id", "raw", "message"}
        }

    @staticmethod
    def _event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in events:
            outcome = event.get("outcome") or "unknown"
            counts[outcome] = counts.get(outcome, 0) + 1
        return counts

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _record_metric(self, outcome: str) -> None:
        if not self._metrics:
            return
        recorder = getattr(self._metrics, "record_owner_notification", None)
        if recorder:
            recorder("executive_brief", outcome)
