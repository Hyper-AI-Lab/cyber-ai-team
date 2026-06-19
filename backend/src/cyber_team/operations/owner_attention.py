"""Owner attention notification delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from cyber_team.clock import utc_now
from cyber_team.config import settings

PRIORITY_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


@dataclass(frozen=True)
class OwnerAttentionEmail:
    to_address: str
    subject: str
    body: str
    agent_id: str | None = "owner-attention-notifier"
    cc: list[str] | None = None
    idempotency_key: str | None = None


class OwnerAttentionNotificationService:
    """Send deduped owner notifications for owner-attention work."""

    EVENT_TYPE = "owner_attention.notification"
    RESOURCE_TYPE = "autonomous_plan"

    def __init__(
        self,
        *,
        planner,
        comms,
        audit_service,
        metrics_service=None,
    ) -> None:
        self._planner = planner
        self._comms = comms
        self._audit = audit_service
        self._metrics = metrics_service

    async def run_once(
        self,
        *,
        actor: str = "owner_attention_notifier",
        limit: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        started_at = utc_now()
        config_status = await self.status()
        if not settings.owner_attention_notifications_enabled:
            return self._empty_result(
                started_at,
                actor=actor,
                status="disabled",
                detail="Owner attention notifications are disabled.",
                config_status=config_status,
            )
        if config_status["status"] not in {"ready", "simulated"}:
            await self._record_system_event(
                actor=actor,
                outcome="skipped",
                action="configuration_check",
                metadata={
                    "reason": config_status["detail"],
                    "notification_status": config_status,
                    "dry_run": dry_run,
                },
            )
            return self._empty_result(
                started_at,
                actor=actor,
                status="configuration_required",
                detail=config_status["detail"],
                config_status=config_status,
            )

        safe_limit = max(1, min(limit or settings.owner_attention_notification_limit, 200))
        queue = await self._planner.list_owner_attention(status="active", limit=safe_limit)
        events = []
        counters = {
            "reviewed": 0,
            "sent": 0,
            "simulated": 0,
            "skipped": 0,
            "failed": 0,
        }
        for item in queue.get("items", []):
            counters["reviewed"] += 1
            if not self._priority_allowed(item):
                counters["skipped"] += 1
                events.append(
                    await self._record_item_event(
                        item,
                        actor=actor,
                        action="skip",
                        outcome="skipped",
                        metadata={
                            "reason": "priority_below_threshold",
                            "min_priority": settings.owner_attention_notification_min_priority,
                            "dry_run": dry_run,
                        },
                    )
                )
                continue

            notification_key = self._notification_key(item)
            if await self._recently_notified(item, notification_key):
                counters["skipped"] += 1
                events.append(
                    await self._record_item_event(
                        item,
                        actor=actor,
                        action="skip",
                        outcome="skipped",
                        metadata={
                            "reason": "recently_notified",
                            "notification_key": notification_key,
                            "cooldown_hours": settings.owner_attention_notification_cooldown_hours,
                            "dry_run": dry_run,
                        },
                    )
                )
                continue

            email = self._email_for_item(item, notification_key)
            if dry_run:
                counters["skipped"] += 1
                events.append(
                    await self._record_item_event(
                        item,
                        actor=actor,
                        action="dry_run",
                        outcome="skipped",
                        metadata={
                            "reason": "dry_run",
                            "notification_key": notification_key,
                            "channel": "email",
                            "recipient": settings.owner_email,
                            "subject": email.subject,
                        },
                    )
                )
                continue

            response = await self._comms.send_email(email)
            response_status = response.get("status") or "unknown"
            if response_status == "sent":
                counters["sent"] += 1
                outcome = "sent"
            elif response_status == "simulated":
                counters["simulated"] += 1
                outcome = "simulated"
            else:
                counters["failed"] += 1
                outcome = "failed"
            events.append(
                await self._record_item_event(
                    item,
                    actor=actor,
                    action="send_email",
                    outcome=outcome,
                    metadata={
                        "notification_key": notification_key,
                        "channel": "email",
                        "recipient": settings.owner_email,
                        "subject": email.subject,
                        "response": response,
                    },
                )
            )
            self._record_metric(outcome)

        completed_at = utc_now()
        status = "ready"
        if counters["failed"]:
            status = "degraded"
        return {
            "enabled": True,
            "status": status,
            "detail": (
                "Owner attention notification run completed with failures."
                if counters["failed"]
                else "Owner attention notification run completed."
            ),
            "actor": actor,
            "dry_run": dry_run,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "queue_counts": queue.get("counts", {}),
            "counts": counters,
            "events": events,
            "notification_status": config_status,
        }

    async def status(self) -> dict[str, Any]:
        email_status = self._email_provider_status()
        if not settings.owner_attention_notifications_enabled:
            status = "disabled"
            detail = "Owner attention notifications are disabled."
        elif not settings.owner_email:
            status = "configuration_required"
            detail = "OWNER_EMAIL is required for owner attention notifications."
        elif email_status.get("mode") == "live":
            status = "ready"
            detail = "Owner attention notifications can send live owner email."
        elif email_status.get("mode") == "simulated":
            status = "simulated"
            detail = "Owner attention notifications will be simulated because SMTP is not live."
        else:
            status = "configuration_required"
            detail = "Live SMTP or communications simulation is required for owner notifications."

        recent_events = []
        last_event = None
        if self._audit:
            recent_events = await self._audit.list_events(
                event_type=self.EVENT_TYPE,
                limit=20,
            )
            last_event = recent_events[0] if recent_events else None
        return {
            "enabled": settings.owner_attention_notifications_enabled,
            "status": status,
            "detail": detail,
            "channel": "email",
            "recipient": settings.owner_email,
            "owner_console_url": settings.owner_console_url,
            "min_priority": settings.owner_attention_notification_min_priority,
            "interval_seconds": max(
                60,
                settings.owner_attention_notification_interval_seconds,
            ),
            "cooldown_hours": settings.owner_attention_notification_cooldown_hours,
            "limit": settings.owner_attention_notification_limit,
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

    def _priority_allowed(self, item: dict[str, Any]) -> bool:
        min_rank = PRIORITY_RANK.get(
            str(settings.owner_attention_notification_min_priority or "medium").lower(),
            PRIORITY_RANK["medium"],
        )
        item_rank = PRIORITY_RANK.get(
            str(item.get("attention_priority") or "medium").lower(),
            PRIORITY_RANK["medium"],
        )
        return item_rank >= min_rank

    async def _recently_notified(self, item: dict[str, Any], notification_key: str) -> bool:
        if not self._audit:
            return False
        events = await self._audit.list_events(
            event_type=self.EVENT_TYPE,
            resource_type=self.RESOURCE_TYPE,
            resource_id=item["plan_id"],
            limit=50,
        )
        cutoff = utc_now() - timedelta(
            hours=max(1, settings.owner_attention_notification_cooldown_hours)
        )
        for event in events:
            metadata = event.get("metadata") or {}
            if metadata.get("notification_key") != notification_key:
                continue
            if event.get("outcome") not in {"sent", "simulated"}:
                continue
            created_at = self._parse_datetime(event.get("created_at"))
            if created_at and created_at >= cutoff:
                return True
        return False

    def _email_for_item(
        self,
        item: dict[str, Any],
        notification_key: str,
    ) -> OwnerAttentionEmail:
        title = str(item.get("title") or "Owner attention required")
        priority = str(item.get("attention_priority") or "medium").upper()
        subject = f"[Cyber-Team] {priority} owner attention: {title[:90]}"
        lines = [
            "Cyber-Team needs owner attention.",
            "",
            f"Title: {title}",
            "Reason: "
            f"{item.get('attention_reason') or item.get('description') or 'Review required.'}",
            f"Priority: {item.get('attention_priority') or 'medium'}",
            f"SLA state: {item.get('sla_state') or 'open'}",
            f"Recommended action: {item.get('recommended_action') or 'review_plan'}",
            f"Plan status: {item.get('status') or 'unknown'}",
            f"Source: {item.get('source_type') or 'unknown'} / {item.get('source_id') or '-'}",
            f"Tasks: {item.get('completed_task_count', 0)}/{item.get('task_count', 0)} completed",
        ]
        if item.get("approval_id"):
            lines.append(f"Approval: {item['approval_id']}")
        if item.get("sla_due_at"):
            lines.append(f"SLA due: {item['sla_due_at']}")
        lines.extend(
            [
                "",
                f"Open Cyber-Team: {settings.owner_console_url}",
                "",
                "This is an owner-control notification. "
                "It does not execute external business writes.",
            ]
        )
        return OwnerAttentionEmail(
            to_address=settings.owner_email,
            subject=subject,
            body="\n".join(lines),
            cc=[],
            idempotency_key=(
                f"owner_attention_notification:{settings.environment}:{notification_key}"
            ),
        )

    def _notification_key(self, item: dict[str, Any]) -> str:
        parts = [
            str(item.get("plan_id") or ""),
            str(item.get("kind") or ""),
            str(item.get("recommended_action") or ""),
            str(item.get("sla_due_at") or ""),
        ]
        return ":".join(parts)

    async def _record_item_event(
        self,
        item: dict[str, Any],
        *,
        actor: str,
        action: str,
        outcome: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = {
            **metadata,
            "plan_id": item.get("plan_id"),
            "kind": item.get("kind"),
            "priority": item.get("attention_priority"),
            "sla_state": item.get("sla_state"),
            "recommended_action": item.get("recommended_action"),
            "target_view": item.get("target_view"),
        }
        if not self._audit:
            return {
                "event_type": self.EVENT_TYPE,
                "actor": actor,
                "resource_type": self.RESOURCE_TYPE,
                "resource_id": item.get("plan_id"),
                "action": action,
                "outcome": outcome,
                "metadata": metadata,
                "created_at": utc_now().isoformat(),
            }
        return await self._audit.record(
            event_type=self.EVENT_TYPE,
            actor=actor,
            actor_type="system",
            resource_type=self.RESOURCE_TYPE,
            resource_id=item.get("plan_id"),
            action=action,
            outcome=outcome,
            metadata=metadata,
        )

    async def _record_system_event(
        self,
        *,
        actor: str,
        outcome: str,
        action: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._audit:
            return {
                "event_type": self.EVENT_TYPE,
                "actor": actor,
                "resource_type": "owner_attention",
                "resource_id": None,
                "action": action,
                "outcome": outcome,
                "metadata": metadata,
                "created_at": utc_now().isoformat(),
            }
        return await self._audit.record(
            event_type=self.EVENT_TYPE,
            actor=actor,
            actor_type="system",
            resource_type="owner_attention",
            resource_id=None,
            action=action,
            outcome=outcome,
            metadata=metadata,
        )

    def _record_metric(self, outcome: str) -> None:
        if self._metrics:
            self._metrics.increment(
                "cyberteam_owner_attention_notifications_total",
                {
                    "channel": "email",
                    "outcome": outcome,
                },
            )

    @staticmethod
    def _event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in events:
            outcome = str(event.get("outcome") or "unknown")
            counts[outcome] = counts.get(outcome, 0) + 1
        return counts

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return parsed

    @staticmethod
    def _empty_result(
        started_at: datetime,
        *,
        actor: str,
        status: str,
        detail: str,
        config_status: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "enabled": settings.owner_attention_notifications_enabled,
            "status": status,
            "detail": detail,
            "actor": actor,
            "dry_run": False,
            "started_at": started_at.isoformat(),
            "completed_at": utc_now().isoformat(),
            "queue_counts": {},
            "counts": {
                "reviewed": 0,
                "sent": 0,
                "simulated": 0,
                "skipped": 0,
                "failed": 0,
            },
            "events": [],
            "notification_status": config_status,
        }
