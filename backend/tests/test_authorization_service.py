import httpx
import pytest

from cyber_team.api.security import Principal
from cyber_team.authorization.service import AuthorizationService


class FakeAudit:
    def __init__(self):
        self.events = []

    async def record(self, **kwargs):
        self.events.append(kwargs)


def _owner() -> Principal:
    return Principal(
        subject="owner",
        email="owner@example.com",
        role="owner",
        token_type="access",
    )


@pytest.mark.asyncio
async def test_identical_allowed_reads_recheck_opa_and_deduplicate_audit(monkeypatch):
    opa_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        opa_calls.append(request)
        return httpx.Response(200, json={"result": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    audit = FakeAudit()
    service = AuthorizationService(audit_service=audit, http_client=client)

    for _ in range(2):
        decision = await service.authorize(
            principal=_owner(),
            action="read",
            resource_type="dashboard",
        )
        assert decision.allowed is True

    assert len(opa_calls) == 2
    assert len(audit.events) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_mutations_and_denials_are_never_audit_deduplicated():
    results = iter((True, True, False, False))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": next(results)})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    audit = FakeAudit()
    service = AuthorizationService(audit_service=audit, http_client=client)

    for _ in range(2):
        await service.authorize(
            principal=_owner(),
            action="execute",
            resource_type="tool",
            resource_id="send_email",
        )
    for _ in range(2):
        await service.authorize(
            principal=_owner(),
            action="read",
            resource_type="secret",
        )

    assert len(audit.events) == 4
    await client.aclose()
