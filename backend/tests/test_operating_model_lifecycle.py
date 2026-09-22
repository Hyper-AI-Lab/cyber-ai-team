import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cyber_team.clock import utc_now
from cyber_team.config import settings
from cyber_team.db import Base
from cyber_team.db.models import (
    Agent,
    AgentCapabilityGrant,
    ApprovalRequest,
    BusinessEvent,
    BusinessWorkItem,
    CompanyClaim,
    CompanyModelRevision,
    CompanySource,
    DiscoveryObligation,
    LifecycleAssessment,
    LifecycleCurrentState,
    OperatingDomain,
    OperatingDomainRevision,
    OperatingLifecycleDecision,
    OperatingModelRevision,
    OperationGraphEdge,
    OperationGraphNode,
    OutsourcingRequest,
    RoleGap,
)
from cyber_team.operations import operating_model as operating_model_module
from cyber_team.operations.operating_model import OperatingModelLifecycleService


def stable_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class FakeToolRegistry:
    def __init__(self):
        self.tools = {
            name: SimpleNamespace(side_effects=False, risk_level="low")
            for name in (
                "approval_request",
                "company_profile_read",
                "memory_recall",
                "memory_remember",
                "process_audit",
            )
        }
        self.tools["send_email"] = SimpleNamespace(
            side_effects=True,
            risk_level="high",
        )

    def get_tool(self, name):
        return self.tools.get(name)

    def get_tool_readiness(self, name):
        tool = self.tools.get(name)
        if not tool:
            return {"state": "configuration_required", "side_effects": False}
        return {
            "state": "live",
            "side_effects": tool.side_effects,
            "risk_level": tool.risk_level,
        }


@pytest.fixture
async def operating_model_session(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @sqlalchemy_event.listens_for(engine.sync_engine, "connect")
    def enforce_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(operating_model_module, "async_session", factory)
    monkeypatch.setattr(settings, "company_namespace", "company:test")
    monkeypatch.setattr(settings, "operating_model_min_confidence", 0.72)
    try:
        yield factory
    finally:
        await engine.dispose()


async def seed_company(factory, *, description: str, claims=None, unknowns=None):
    model_payload = {"business_description": description}
    model = CompanyModelRevision(
        id="company-model-1",
        company_namespace="company:test",
        revision=1,
        status="active",
        model=model_payload,
        claim_ids=[],
        unknowns=list(unknowns or []),
        disputes=[],
        provenance_coverage=0.9,
        confidence=0.9,
        source_hash=stable_hash(model_payload),
        activated_at=utc_now(),
    )
    rows = [model]
    for index, claim in enumerate(claims or []):
        claim_payload = {
            "subject": "company",
            "predicate": claim[0],
            "value": claim[1],
        }
        row = CompanyClaim(
            id=f"claim-{index}",
            company_namespace="company:test",
            subject="company",
            predicate=claim[0],
            value=claim[1],
            epistemic_state="verified",
            confidence=0.9,
            trust_class="canonical",
            evidence_ids=[f"evidence-{index}"],
            claim_hash=stable_hash(claim_payload),
        )
        model.claim_ids.append(row.id)
        rows.append(row)
    async with factory() as session:
        session.add_all(rows)
        await session.commit()


class FakeCompanyIntelligence:
    def __init__(self, *, unresolved: list[str]):
        self.unresolved = unresolved
        self.acquisitions = 0

    async def ensure_default_sources(self, _namespace):
        return []

    async def acquire_available_evidence(self, *, company_namespace):
        self.acquisitions += 1
        return {"status": "completed", "company_namespace": company_namespace}

    async def discover_company_model(self, **_kwargs):
        return {
            "id": "discovered-model",
            "company_namespace": "company:test",
            "model": {"legal_name": "Verified Company"} if not self.unresolved else {},
            "unknowns": list(self.unresolved),
        }

    async def research_model_unknowns(self, _model):
        return {"status": "completed", "created": 0, "queries": []}


async def test_synthesis_is_evidence_driven_versioned_and_idempotent(
    operating_model_session,
):
    await seed_company(
        operating_model_session,
        description="A B2B software product with customer support and a sales pipeline.",
    )
    service = OperatingModelLifecycleService()

    first = await service.synthesize()
    second = await service.synthesize()

    assert first["reused"] is False
    assert second["reused"] is True
    assert second["id"] == first["id"]
    assert {"company_builder", "governance", "knowledge", "security", "supervisor"} <= set(
        first["domain_keys"]
    )
    assert {"product", "sales", "support"} <= set(first["domain_keys"])
    assert "finance" not in first["domain_keys"]
    noncore = [item for item in first["summary"]["domains"] if not item["core"]]
    assert all(item["evidence"] for item in noncore)
    assert first["source_hash"] == second["source_hash"]


async def test_synthesis_identity_ignores_repeated_provenance_observations(
    operating_model_session,
):
    await seed_company(
        operating_model_session,
        description="A B2B software product with customer support.",
        claims=[("customer_support", {"enabled": True})],
    )
    service = OperatingModelLifecycleService()

    first = await service.synthesize()
    async with operating_model_session() as session:
        claim = (
            await session.execute(
                select(CompanyClaim).where(
                    CompanyClaim.predicate == "customer_support"
                )
            )
        ).scalar_one()
        claim.evidence_ids = ["evidence-new", "evidence-replayed"]
        await session.commit()
    repeated = await service.synthesize()

    assert repeated["reused"] is True
    assert repeated["id"] == first["id"]
    assert repeated["source_hash"] == first["source_hash"]


@pytest.mark.parametrize(
    ("description", "expected", "excluded"),
    [
        (
            "A B2B SaaS software product with a sales pipeline and customer support.",
            {"product", "sales", "support"},
            {"finance", "hr"},
        ),
        (
            "A digital consultancy delivering client projects under service contracts.",
            {"product", "sales", "legal"},
            {"finance", "hr", "support"},
        ),
        (
            "An e-commerce retailer with invoices, warehouse inventory, suppliers, "
            "procurement, and customer support.",
            {"finance", "operations", "sales", "support"},
            {"hr", "product"},
        ),
        (
            "A regulated professional service with employee capacity, privacy, legal "
            "jurisdiction, and compliance obligations.",
            {"hr", "legal", "security"},
            {"finance", "product", "support"},
        ),
    ],
    ids=["b2b-saas", "digital-consultancy", "e-commerce", "regulated-services"],
)
async def test_offline_company_archetypes_derive_distinct_supported_domains(
    operating_model_session,
    description,
    expected,
    excluded,
):
    await seed_company(operating_model_session, description=description)

    result = await OperatingModelLifecycleService().synthesize()
    derived = set(result["domain_keys"])

    assert expected <= derived
    assert derived.isdisjoint(excluded)
    assert all(item["core"] or item["evidence"] for item in result["summary"]["domains"])


async def test_evidence_derived_custom_domain_is_data_only_and_bounded(
    operating_model_session,
):
    custom = {
        "specification": {
            "key": "partner_success",
            "display_name": "Partner Success",
            "purpose": "Coordinate verified partner outcomes.",
            "inputs": ["partner_signal"],
            "outputs": ["partner_assessment"],
            "event_selectors": ["partner"],
            "evidence_selectors": ["partner"],
            "required_capabilities": ["partner_management"],
            "default_role_name": "Partner Success Agent",
        }
    }
    await seed_company(
        operating_model_session,
        description="A consultancy operating a verified partner network.",
        claims=[("operating_domain", custom)],
    )

    result = await OperatingModelLifecycleService().synthesize()

    assert "partner_success" in result["domain_keys"]
    item = next(
        value for value in result["summary"]["domains"] if value["key"] == "partner_success"
    )
    assert "executor" not in item
    assert item["required_capabilities"] == ["partner_management"]
    assert {"claim-0", "evidence-0"} == set(item["evidence_ids"])


async def test_observer_activates_valid_revision_and_reuses_review(
    operating_model_session,
):
    await seed_company(
        operating_model_session,
        description="A digital consultancy delivering customer projects.",
    )
    service = OperatingModelLifecycleService()
    revision = await service.synthesize()

    first = await service.review_revision(revision["id"])
    second = await service.review_revision(revision["id"])
    latest = await service.latest()

    assert first["status"] == "agreed"
    assert second["reused"] is True
    assert latest["status"] == "active"
    assert latest["observer_review_id"] == first["id"]


async def test_observer_preserves_previous_model_on_unproven_domain(
    operating_model_session,
):
    await seed_company(
        operating_model_session,
        description="A software product business.",
    )
    service = OperatingModelLifecycleService()
    revision = await service.synthesize()
    async with operating_model_session() as session:
        row = await session.get(operating_model_module.OperatingModelRevision, revision["id"])
        row.summary = {
            **row.summary,
            "domains": [
                *row.summary["domains"],
                {
                    "key": "invented_domain",
                    "core": False,
                    "confidence": 0.99,
                    "evidence": [],
                    "required_tools": [],
                },
            ],
        }
        await session.commit()

    review = await service.review_revision(revision["id"])
    latest = await service.latest()

    assert review["status"] == "disagreed"
    assert "domain_provenance_missing" in {item["code"] for item in review["unresolved_objections"]}
    assert latest["status"] == "owner_review"


async def test_reconciler_qualifies_shadow_then_becomes_idempotent(
    operating_model_session,
):
    await seed_company(
        operating_model_session,
        description="A B2B software product with customer support.",
    )
    service = OperatingModelLifecycleService()
    revision = await service.synthesize()
    await service.review_revision(revision["id"])

    first = await service.reconcile()
    second = await service.reconcile()
    third = await service.reconcile()
    async with operating_model_session() as session:
        rows = (
            (
                await session.execute(
                    select(OperatingDomain).where(
                        OperatingDomain.company_namespace == "company:test"
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.shadow_started_at = utc_now() - timedelta(hours=2)
        await session.commit()
    promoted = await service.reconcile()
    stable = await service.reconcile()
    repeated = await service.reconcile()

    assert first["status"] == "completed"
    assert first["summary"]["counts"]["start_shadow"] >= 1
    assert second["summary"]["counts"]["record_shadow_success"] >= 1
    assert third["summary"]["counts"]["record_shadow_success"] >= 1
    assert promoted["summary"]["counts"]["activate"] >= 1
    assert stable["summary"]["counts"]["noop"] >= 1
    assert repeated["reused"] is True
    latest = await service.latest()
    assert latest
    assert all(
        item["lifecycle_state"] == "active" for item in latest["actual_domains"]
    )
    async with operating_model_session() as session:
        lifecycle_decisions = (
            await session.execute(select(OperatingLifecycleDecision))
        ).scalars().all()
        graph_nodes = (await session.execute(select(OperationGraphNode))).scalars().all()
        graph_edges = (await session.execute(select(OperationGraphEdge))).scalars().all()
    assert lifecycle_decisions
    assert all(item.operation_node_id for item in lifecycle_decisions)
    assert all(
        item.policy_decision.get("decision_reference") for item in lifecycle_decisions
    )
    assert len(graph_nodes) >= len(lifecycle_decisions) + 2
    assert len(graph_edges) >= len(lifecycle_decisions) + 1


async def test_reconciliation_dry_run_records_without_mutating_domains(
    operating_model_session,
):
    await seed_company(
        operating_model_session,
        description="An e-commerce business with inventory and customer support.",
    )
    service = OperatingModelLifecycleService()
    revision = await service.synthesize()
    await service.review_revision(revision["id"])

    result = await service.reconcile(dry_run=True)
    latest = await service.latest()

    assert result["status"] == "dry_run"
    assert result["summary"]["counts"]["start_shadow"] >= 1
    assert latest["actual_domains"] == []


async def test_reconciliation_fails_closed_when_opa_denies_transition(
    operating_model_session,
):
    await seed_company(
        operating_model_session,
        description="A software company.",
    )
    policy = AsyncMock()
    policy.evaluate.return_value = {
        "allowed": False,
        "requires_approval": False,
        "reasons": ["opa_unavailable_fail_closed"],
        "source": "fail_closed",
        "decision_reference": "opa_unavailable_test",
    }
    service = OperatingModelLifecycleService(action_policy_service=policy)
    revision = await service.synthesize()
    await service.review_revision(revision["id"])

    result = await service.reconcile()

    assert result["status"] == "blocked"
    assert result["summary"]["counts"]["policy_blocked"] >= 1
    async with operating_model_session() as session:
        domains = (await session.execute(select(OperatingDomain))).scalars().all()
        decisions = (
            await session.execute(select(OperatingLifecycleDecision))
        ).scalars().all()
    assert domains == []
    assert all(item.decision_status == "blocked" for item in decisions)
    assert all(
        item.policy_decision["decision_reference"] == "opa_unavailable_test"
        for item in decisions
    )


async def test_role_convergence_provisions_safe_authority_and_gates_side_effects(
    operating_model_session,
):
    await seed_company(
        operating_model_session,
        description="A B2B software product with customer support.",
    )
    work = SimpleNamespace(
        ensure_active_agent_mandates=AsyncMock(return_value={"status": "completed"})
    )
    service = OperatingModelLifecycleService(
        tool_registry=FakeToolRegistry(),
        work_portfolio_service=work,
    )
    revision = await service.synthesize()
    await service.review_revision(revision["id"])
    await service.reconcile()
    async with operating_model_session() as session:
        product = (
            await session.execute(
                select(OperatingDomain).where(OperatingDomain.domain_key == "product")
            )
        ).scalar_one()
        specification = (
            await session.execute(
                select(OperatingDomainRevision).where(
                    OperatingDomainRevision.domain_id == product.id
                )
            )
        ).scalar_one()
        specification.required_tools = ["send_email", "unconfigured_connector"]
        await session.commit()

    first = await service.converge_roles_and_mandates()
    second = await service.converge_roles_and_mandates()

    async with operating_model_session() as session:
        agents = (await session.execute(select(Agent))).scalars().all()
        grants = (await session.execute(select(AgentCapabilityGrant))).scalars().all()
        approvals = (await session.execute(select(ApprovalRequest))).scalars().all()
        gaps = (await session.execute(select(RoleGap))).scalars().all()
    product_agent = next(item for item in agents if item.role_family == "product")
    product_grants = [item for item in grants if item.agent_id == product_agent.id]
    assert first["counts"]["agents_created"] == len(agents)
    assert second["counts"].get("agents_created", 0) == 0
    assert "send_email" not in product_agent.tools
    assert any(
        item.tool_name == "send_email" and item.state == "pending_approval"
        for item in product_grants
    )
    assert any(item.tool_name == "memory_recall" for item in product_grants)
    assert len(approvals) == 1
    assert approvals[0].target_id == f"{product_agent.id}:send_email"
    assert len(gaps) == 1
    assert gaps[0].capability == "unconfigured_connector"
    work.ensure_active_agent_mandates.assert_awaited()


async def test_backlog_reconciliation_supersedes_obsolete_work_and_approval(
    operating_model_session,
):
    await seed_company(
        operating_model_session,
        description="A software product with customer support.",
    )
    service = OperatingModelLifecycleService(tool_registry=FakeToolRegistry())
    revision = await service.synthesize()
    await service.review_revision(revision["id"])
    await service.reconcile()
    async with operating_model_session() as session:
        finance_gap = RoleGap(
            id="finance-gap",
            title="Finance specialist",
            description="Prepare accounting work.",
            status="proposed",
            severity="medium",
            source_type="operating_model",
            company_namespace="company:test",
            capability="finance",
            context={"domain_key": "finance", "operating_model_revision_id": "old-model"},
        )
        current_gap = RoleGap(
            id="product-gap",
            title="Product research capability",
            description="Research the product roadmap.",
            status="open",
            severity="low",
            source_type="operating_model",
            company_namespace="company:test",
            capability="product",
            requested_tools=["memory_recall"],
            context={"domain_key": "product", "operating_model_revision_id": revision["id"]},
        )
        approval = ApprovalRequest(
            id="obsolete-approval",
            action_type="role_gap_apply",
            action_description="Apply obsolete finance role gap.",
            action_payload={
                "domain_key": "finance",
                "operating_model_revision_id": "old-model",
            },
            requester="chief_operating_agent",
            requester_type="agent",
            risk_level="medium",
            target_type="role_gap",
            target_id=finance_gap.id,
            status="pending",
            expires_at=utc_now() + timedelta(hours=1),
        )
        outsource = OutsourcingRequest(
            id="obsolete-outsourcing",
            title="Build finance connector",
            status="open",
            complexity_reason="Accounting integration is missing.",
            task_spec={"domain_key": "finance"},
            context_pack={},
        )
        completed_work = BusinessWorkItem(
            id="completed-product-work",
            company_namespace="company:test",
            title="Completed product research",
            work_type="domain_operation",
            status="completed",
            payload={"domain_key": "product"},
            idempotency_key="completed-product-work",
        )
        session.add_all(
            [finance_gap, current_gap, approval, outsource, completed_work]
        )
        await session.commit()

    first = await service.reconcile_backlogs()
    second = await service.reconcile_backlogs()

    async with operating_model_session() as session:
        approval = await session.get(ApprovalRequest, "obsolete-approval")
        finance_gap = await session.get(RoleGap, "finance-gap")
        current_gap = await session.get(RoleGap, "product-gap")
        assessments = (await session.execute(select(LifecycleAssessment))).scalars().all()
        current_states = (
            await session.execute(select(LifecycleCurrentState))
        ).scalars().all()
    assert "obsolete-approval" in first["invalidated_approval_ids"]
    assert approval.status == "expired"
    assert finance_gap.context["lifecycle_status"] == "superseded"
    assert current_gap.context["lifecycle_status"] == "actionable"
    assert second["invalidated_approval_ids"] == []
    assert first["assessment_count"] == 5
    assert first["transition_count"] == 5
    assert second["assessment_count"] == 3
    assert second["transition_count"] == 0
    assert second["unchanged_count"] == 2
    assert len(current_states) == 5
    assert len(assessments) == len(
        {(item.resource_type, item.resource_id, item.lifecycle_status) for item in assessments}
    )
    assert all(item.idempotency_key.startswith("lifecycle:v2:") for item in assessments)

    async with operating_model_session() as session:
        active_model = (
            await session.execute(
                select(OperatingModelRevision).where(
                    OperatingModelRevision.status == "active"
                )
            )
        ).scalar_one()
        active_model.status = "superseded"
        session.add(
            OperatingModelRevision(
                id="replacement-operating-model",
                company_namespace=active_model.company_namespace,
                revision=active_model.revision + 1,
                status="active",
                company_model_revision_id=active_model.company_model_revision_id,
                strategy_context_hash=active_model.strategy_context_hash,
                source_hash="replacement-operating-model-source",
                summary=active_model.summary,
                domain_keys=active_model.domain_keys,
                objective_revision_ids=active_model.objective_revision_ids,
                evidence_ids=active_model.evidence_ids,
                confidence=active_model.confidence,
                observer_review_id=active_model.observer_review_id,
                created_by="test",
                activated_at=utc_now(),
            )
        )
        await session.commit()

    after_revision = await service.reconcile_backlogs()
    async with operating_model_session() as session:
        assessments_after_revision = (
            await session.execute(select(LifecycleAssessment))
        ).scalars().all()
        current_states_after_revision = (
            await session.execute(select(LifecycleCurrentState))
        ).scalars().all()
    assert len(assessments_after_revision) == len(assessments)
    assert after_revision["assessment_count"] == 5
    assert after_revision["transition_count"] == 0
    assert all(
        item.operating_model_revision_id == "replacement-operating-model"
        for item in current_states_after_revision
    )


async def test_discovery_obligations_are_deduplicated_and_source_bounded(
    operating_model_session,
):
    await seed_company(
        operating_model_session,
        description="A software company.",
        unknowns=["legal_name", "customer_segments"],
    )
    async with operating_model_session() as session:
        session.add_all(
            [
                CompanySource(
                    id="source-erpnext",
                    company_namespace="company:test",
                    source_key="erpnext",
                    source_type="erpnext",
                    name="ERPNext",
                    status="active",
                ),
                CompanySource(
                    id="source-research",
                    company_namespace="company:test",
                    source_key="public_research",
                    source_type="searxng",
                    name="Public research",
                    status="active",
                ),
            ]
        )
        await session.commit()
    service = OperatingModelLifecycleService()

    first = await service.reconcile_discovery_obligations(run_attempts=False)
    second = await service.reconcile_discovery_obligations(run_attempts=False)

    assert first["created"] == 2
    assert second["created"] == 0
    assert second["reused"] == 2
    items = {item["predicate"]: item for item in await service.list_discovery_obligations()}
    assert items["legal_name"]["source_types"] == ["erpnext"]
    assert items["customer_segments"]["source_types"] == ["erpnext", "searxng"]


async def test_discovery_attempt_resolves_when_company_model_unknown_clears(
    operating_model_session,
):
    await seed_company(
        operating_model_session,
        description="A software company.",
        unknowns=["legal_name"],
    )
    intelligence = FakeCompanyIntelligence(unresolved=[])
    service = OperatingModelLifecycleService(company_intelligence_service=intelligence)

    result = await service.reconcile_discovery_obligations()

    assert result["processing"]["attempted"] == 1
    assert result["processing"]["items"][0]["status"] == "resolved"
    assert intelligence.acquisitions == 1


async def test_resolved_discovery_obligation_is_reused_for_same_model_generation(
    operating_model_session,
):
    await seed_company(
        operating_model_session,
        description="A software company.",
        unknowns=["legal_name"],
    )
    intelligence = FakeCompanyIntelligence(unresolved=[])
    service = OperatingModelLifecycleService(company_intelligence_service=intelligence)

    first = await service.reconcile_discovery_obligations()
    second = await service.reconcile_discovery_obligations(run_attempts=False)

    assert first["processing"]["items"][0]["status"] == "resolved"
    assert second["created"] == 0
    assert second["reused"] == 1
    assert second["items"][0]["status"] == "resolved"
    async with operating_model_session() as session:
        obligations = (await session.execute(select(DiscoveryObligation))).scalars().all()
    assert len(obligations) == 1


async def test_discovery_exhaustion_creates_one_owner_attention_event(
    operating_model_session,
    monkeypatch,
):
    monkeypatch.setattr(settings, "discovery_obligation_max_attempts", 1)
    await seed_company(
        operating_model_session,
        description="A software company.",
        unknowns=["legal_name"],
    )
    intelligence = FakeCompanyIntelligence(unresolved=["legal_name"])
    service = OperatingModelLifecycleService(company_intelligence_service=intelligence)

    first = await service.reconcile_discovery_obligations()
    second = await service.reconcile_discovery_obligations()

    item = first["processing"]["items"][0]
    assert item["status"] == "owner_review"
    assert item["owner_attention_id"]
    assert second["processing"]["attempted"] == 0
    async with operating_model_session() as session:
        obligations = (await session.execute(select(DiscoveryObligation))).scalars().all()
        events = (await session.execute(select(BusinessEvent))).scalars().all()
    assert len(obligations) == 1
    assert obligations[0].attempts == 1
    assert len(events) == 1
    assert events[0].disposition == "owner_escalation"
