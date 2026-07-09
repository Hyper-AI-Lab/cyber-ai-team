from datetime import timedelta

import pytest

from cyber_team.clock import utc_now
from cyber_team.operations.executive_briefing import ExecutiveBriefEmailService


class FakeExecutive:
    async def executive_brief(self):
        return {
            "latest_run": {
                "run_id": "exegov_1",
                "status": "completed",
                "blocked_actions": [],
                "approvals_created": [],
                "reflection_summary": {"next_watch_items": ["Watch benchmark drift"]},
            },
            "objectives": {
                "count": 1,
                "items": [
                    {
                        "title": "Maintain autonomous continuity",
                        "priority": "high",
                        "status": "active",
                    }
                ],
            },
            "kpis": {"count": 1, "items": [{"key": "readiness", "value": "ready"}]},
            "benchmarks": {
                "latest_results": {
                    "count": 1,
                    "items": [{"benchmark_key": "readiness_ready", "status": "passed"}],
                }
            },
            "observer": {"status": "ready"},
            "outsourcing": {"count": 0},
            "resource_policy": {"status": "ready"},
            "readiness": {"status": "ready"},
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
            }
        ]

    async def send_email(self, data):
        self.sent.append(data)
        return {"email_id": "email-1", "status": "sent", "provider": "smtp"}


class FakeAudit:
    def __init__(self, events=None):
        self.events = list(events or [])
        self.recorded = []

    async def list_events(self, **kwargs):
        return list(self.events)

    async def record(self, **kwargs):
        event = {
            "id": f"event-{len(self.recorded) + 1}",
            "created_at": utc_now().isoformat(),
            **kwargs,
        }
        self.recorded.append(event)
        return event


@pytest.mark.asyncio
async def test_executive_brief_email_sends_live_owner_digest(monkeypatch):
    monkeypatch.setattr(
        "cyber_team.operations.executive_briefing.settings.executive_brief_email_enabled",
        True,
    )
    monkeypatch.setattr(
        "cyber_team.operations.executive_briefing.settings.owner_email",
        "owner@example.com",
    )
    monkeypatch.setattr(
        "cyber_team.operations.executive_briefing.settings.owner_console_url",
        "https://cyberteam.example.com",
    )
    audit = FakeAudit()
    comms = FakeComms()
    service = ExecutiveBriefEmailService(
        executive_service=FakeExecutive(),
        comms=comms,
        audit_service=audit,
    )

    result = await service.run_once(actor="scheduler")

    assert result["status"] == "ready"
    assert result["response"]["status"] == "sent"
    assert result["brief_summary"]["latest_run_id"] == "exegov_1"
    assert len(comms.sent) == 1
    assert comms.sent[0].to_address == "owner@example.com"
    assert "Cyber-Team Executive Brief" in comms.sent[0].body
    assert audit.recorded[-1]["event_type"] == "executive_brief.email"
    assert audit.recorded[-1]["outcome"] == "sent"


@pytest.mark.asyncio
async def test_executive_brief_email_skips_recent_delivery(monkeypatch):
    monkeypatch.setattr(
        "cyber_team.operations.executive_briefing.settings.executive_brief_email_enabled",
        True,
    )
    monkeypatch.setattr(
        "cyber_team.operations.executive_briefing.settings.owner_email",
        "owner@example.com",
    )
    monkeypatch.setattr(
        "cyber_team.operations.executive_briefing.settings.executive_brief_email_cooldown_hours",
        20,
    )
    audit = FakeAudit(
        events=[
            {
                "id": "event-old",
                "outcome": "sent",
                "created_at": (utc_now() - timedelta(hours=2)).isoformat(),
            }
        ]
    )
    comms = FakeComms()
    service = ExecutiveBriefEmailService(
        executive_service=FakeExecutive(),
        comms=comms,
        audit_service=audit,
    )

    result = await service.run_once(actor="scheduler")

    assert result["status"] == "skipped"
    assert result["brief_summary"] is None
    assert comms.sent == []
    assert audit.recorded[-1]["metadata"]["reason"] == "recently_sent"


@pytest.mark.asyncio
async def test_executive_brief_email_force_bypasses_cooldown(monkeypatch):
    monkeypatch.setattr(
        "cyber_team.operations.executive_briefing.settings.executive_brief_email_enabled",
        True,
    )
    monkeypatch.setattr(
        "cyber_team.operations.executive_briefing.settings.owner_email",
        "owner@example.com",
    )
    audit = FakeAudit(
        events=[
            {
                "id": "event-old",
                "outcome": "sent",
                "created_at": utc_now().isoformat(),
            }
        ]
    )
    comms = FakeComms()
    service = ExecutiveBriefEmailService(
        executive_service=FakeExecutive(),
        comms=comms,
        audit_service=audit,
    )

    result = await service.run_once(actor="owner@example.com", force=True)

    assert result["status"] == "ready"
    assert len(comms.sent) == 1
    assert ":force:" in comms.sent[0].idempotency_key
