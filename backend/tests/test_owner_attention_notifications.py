from datetime import datetime, timedelta

from cyber_team.operations.owner_attention import OwnerAttentionNotificationService


class FakePlanner:
    async def list_owner_attention(self, status="active", limit=100):
        return {
            "generated_at": "2026-06-19T00:00:00",
            "counts": {"total": 1, "active": 1},
            "items": [
                {
                    "plan_id": "plan_1",
                    "title": "Finance operating review",
                    "description": "Review finance cadence.",
                    "source_type": "operating_cadence",
                    "source_id": "cadence:finance",
                    "status": "planned",
                    "kind": "scheduled_operating_cadence",
                    "attention_priority": "medium",
                    "attention_reason": "Scheduled finance review is due.",
                    "recommended_action": "execute_plan",
                    "target_view": "operations",
                    "sla_state": "open",
                    "sla_due_at": "2026-06-20T00:00:00",
                    "completed_task_count": 0,
                    "task_count": 2,
                }
            ],
        }


class FakeComms:
    def __init__(self):
        self.sent = []

    def integration_status(self):
        return [
            {
                "channel": "email",
                "provider": "smtp",
                "configured": True,
                "mode": "live",
                "detail": "SMTP ready.",
            }
        ]

    async def send_email(self, data):
        self.sent.append(data)
        return {"email_id": f"email-{len(self.sent)}", "status": "sent", "provider": "smtp"}


FIXED_NOW = datetime(2026, 6, 19, 12, 0, 0)


class FakeAudit:
    def __init__(self, *, created_at=None):
        self.events = []
        self.created_at = created_at or FIXED_NOW

    async def list_events(
        self,
        limit=100,
        event_type=None,
        actor=None,
        resource_type=None,
        resource_id=None,
    ):
        events = [
            event
            for event in self.events
            if (not event_type or event["event_type"] == event_type)
            and (not actor or event["actor"] == actor)
            and (not resource_type or event["resource_type"] == resource_type)
            and (not resource_id or event["resource_id"] == resource_id)
        ]
        return list(reversed(events))[:limit]

    async def record(
        self,
        *,
        event_type,
        actor="system",
        actor_type="system",
        resource_type=None,
        resource_id=None,
        action=None,
        outcome="success",
        metadata=None,
    ):
        event = {
            "id": f"event-{len(self.events) + 1}",
            "event_type": event_type,
            "actor": actor,
            "actor_type": actor_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "action": action,
            "outcome": outcome,
            "metadata": metadata or {},
            "created_at": self.created_at.isoformat(),
        }
        self.events.append(event)
        return event


async def test_owner_attention_notification_sends_and_dedupes(monkeypatch):
    monkeypatch.setattr(
        "cyber_team.operations.owner_attention.utc_now",
        lambda: FIXED_NOW,
    )
    monkeypatch.setattr(
        "cyber_team.operations.owner_attention.settings.owner_email",
        "owner@example.com",
    )
    monkeypatch.setattr(
        "cyber_team.operations.owner_attention.settings.owner_console_url",
        "https://cyberteam.example.com",
    )
    monkeypatch.setattr(
        "cyber_team.operations.owner_attention.settings.owner_attention_notifications_enabled",
        True,
    )
    monkeypatch.setattr(
        "cyber_team.operations.owner_attention.settings.owner_attention_notification_min_priority",
        "medium",
    )
    monkeypatch.setattr(
        "cyber_team.operations.owner_attention.settings.owner_attention_notification_cooldown_hours",
        24,
    )
    comms = FakeComms()
    audit = FakeAudit()
    service = OwnerAttentionNotificationService(
        planner=FakePlanner(),
        comms=comms,
        audit_service=audit,
    )

    first = await service.run_once(actor="test-runner")
    second = await service.run_once(actor="test-runner")

    assert first["counts"]["sent"] == 1
    assert first["events"][0]["outcome"] == "sent"
    assert comms.sent[0].to_address == "owner@example.com"
    assert "Finance operating review" in comms.sent[0].subject
    assert "https://cyberteam.example.com" in comms.sent[0].body
    assert second["counts"]["sent"] == 0
    assert second["counts"]["skipped"] == 1
    assert second["events"][0]["metadata"]["reason"] == "recently_notified"
    assert len(comms.sent) == 1


async def test_owner_attention_notification_resends_after_cooldown(monkeypatch):
    monkeypatch.setattr(
        "cyber_team.operations.owner_attention.utc_now",
        lambda: FIXED_NOW,
    )
    monkeypatch.setattr(
        "cyber_team.operations.owner_attention.settings.owner_email",
        "owner@example.com",
    )
    monkeypatch.setattr(
        "cyber_team.operations.owner_attention.settings.owner_attention_notifications_enabled",
        True,
    )
    monkeypatch.setattr(
        "cyber_team.operations.owner_attention.settings.owner_attention_notification_cooldown_hours",
        24,
    )
    comms = FakeComms()
    audit = FakeAudit(created_at=FIXED_NOW - timedelta(hours=25))
    service = OwnerAttentionNotificationService(
        planner=FakePlanner(),
        comms=comms,
        audit_service=audit,
    )

    first = await service.run_once(actor="test-runner")
    second = await service.run_once(actor="test-runner")

    assert first["counts"]["sent"] == 1
    assert second["counts"]["sent"] == 1
    assert len(comms.sent) == 2


async def test_owner_attention_notification_dry_run_records_no_delivery(monkeypatch):
    monkeypatch.setattr(
        "cyber_team.operations.owner_attention.utc_now",
        lambda: FIXED_NOW,
    )
    monkeypatch.setattr(
        "cyber_team.operations.owner_attention.settings.owner_email",
        "owner@example.com",
    )
    monkeypatch.setattr(
        "cyber_team.operations.owner_attention.settings.owner_attention_notifications_enabled",
        True,
    )
    comms = FakeComms()
    audit = FakeAudit()
    service = OwnerAttentionNotificationService(
        planner=FakePlanner(),
        comms=comms,
        audit_service=audit,
    )

    result = await service.run_once(actor="owner@example.com", dry_run=True)

    assert result["dry_run"] is True
    assert result["counts"]["skipped"] == 1
    assert result["events"][0]["action"] == "dry_run"
    assert comms.sent == []
