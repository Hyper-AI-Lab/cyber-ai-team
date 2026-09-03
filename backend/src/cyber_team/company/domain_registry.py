"""Declarative operating-domain contracts and deterministic event routing."""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator

DOMAIN_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,99}$")
DOMAIN_ALIASES = {
    "compliance": "security",
    "observer": "governance",
    "people": "hr",
    "project_management": "product",
    "research": "knowledge",
}


class DomainSpecification(BaseModel):
    """Data-only contract consumed by the universal role and routing loops."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    display_name: str
    purpose: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    event_selectors: list[str] = Field(default_factory=list)
    evidence_selectors: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    default_role_name: str
    core: bool = False
    risk_level: str = "low"
    cadence_seconds: int = 900

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        normalized = canonical_domain_key(value)
        if not DOMAIN_KEY_PATTERN.fullmatch(normalized):
            raise ValueError("Domain key must be a lower-case snake-case identifier")
        return normalized

    @field_validator("risk_level")
    @classmethod
    def validate_risk(cls, value: str) -> str:
        if value not in {"low", "medium", "high", "critical"}:
            raise ValueError("Unsupported domain risk level")
        return value

    @field_validator("cadence_seconds")
    @classmethod
    def validate_cadence(cls, value: int) -> int:
        if not 60 <= value <= 604_800:
            raise ValueError("Domain cadence must be between one minute and seven days")
        return value


class DomainRegistry:
    """Validated domain catalog; custom entries never carry executable code."""

    def __init__(
        self,
        specifications: Iterable[DomainSpecification | dict] = (),
        *,
        max_domains: int = 25,
    ) -> None:
        self._max_domains = max(1, min(int(max_domains), 100))
        self._specifications: dict[str, DomainSpecification] = {}
        self.extend(specifications)

    @classmethod
    def builtin(cls, *, max_domains: int = 25) -> DomainRegistry:
        return cls(BUILTIN_DOMAIN_SPECIFICATIONS, max_domains=max_domains)

    def extend(self, specifications: Iterable[DomainSpecification | dict]) -> None:
        additions = [
            item
            if isinstance(item, DomainSpecification)
            else DomainSpecification.model_validate(item)
            for item in specifications
        ]
        if len(self._specifications) + len(additions) > self._max_domains:
            raise ValueError(f"Operating model cannot exceed {self._max_domains} domains")
        for item in additions:
            if item.key in self._specifications:
                raise ValueError(f"Duplicate operating domain: {item.key}")
            self._specifications[item.key] = item

    def get(self, key: str) -> DomainSpecification | None:
        return self._specifications.get(canonical_domain_key(key))

    def require(self, key: str) -> DomainSpecification:
        item = self.get(key)
        if not item:
            raise ValueError(f"Unknown company operating domain: {canonical_domain_key(key)}")
        return item

    def keys(self) -> list[str]:
        return sorted(self._specifications)

    def specifications(self) -> list[DomainSpecification]:
        return [self._specifications[key] for key in self.keys()]

    def route_event(self, event_type: str, payload_text: str = "") -> str:
        normalized = str(event_type or "").removeprefix("evidence.")
        matches = []
        for item in self.specifications():
            specificity = max(
                (
                    len(selector)
                    for selector in item.event_selectors
                    if normalized == selector or normalized.startswith(selector + ".")
                ),
                default=0,
            )
            if specificity:
                matches.append((specificity, item.key))
        if matches:
            return sorted(matches, key=lambda value: (-value[0], value[1]))[0][1]
        lowered = payload_text.lower()
        for markers, family in (
            (("invoice", "payment", "expense", "cash"), "finance"),
            (("contract", "legal", "privacy", "tax"), "legal"),
            (("security", "credential", "auth", "injection"), "security"),
        ):
            if any(marker in lowered for marker in markers) and family in self._specifications:
                return family
        return "operations" if "operations" in self._specifications else self.keys()[0]


def canonical_domain_key(value: str) -> str:
    normalized = str(value or "operations").strip().lower().replace("-", "_").replace(" ", "_")
    return DOMAIN_ALIASES.get(normalized, normalized)


BUILTIN_DOMAIN_SPECIFICATIONS = (
    {
        "key": "company_builder",
        "display_name": "Company Builder",
        "purpose": "Maintain the evidence-backed company model and operating design.",
        "inputs": ["company_model", "company_claim", "role_gap"],
        "outputs": ["company_model_revision", "role_proposal", "capability_gap"],
        "event_selectors": ["erpnext.company_context_snapshot"],
        "evidence_selectors": ["company", "business model", "operating model"],
        "required_capabilities": ["company_discovery", "operating_model_design"],
        "default_role_name": "Company Builder",
        "core": True,
    },
    {
        "key": "finance",
        "display_name": "Finance",
        "purpose": "Analyze financial state and prepare governed financial work.",
        "inputs": ["erpnext.sales_invoice", "erpnext.account", "erpnext.opportunity"],
        "outputs": ["financial_analysis", "forecast", "approval_backed_financial_action"],
        "event_selectors": ["erpnext.sales_invoice", "erpnext.account"],
        "evidence_selectors": [
            "finance",
            "accounting",
            "revenue",
            "invoice",
            "payment",
            "budget",
            "tax",
        ],
        "required_capabilities": ["financial_analysis"],
        "default_role_name": "Finance Agent",
        "risk_level": "high",
    },
    {
        "key": "legal",
        "display_name": "Legal",
        "purpose": "Identify legal obligations and prepare owner-gated legal work.",
        "inputs": ["company_claim.jurisdiction", "contract", "policy", "regulation"],
        "outputs": ["legal_analysis", "policy_draft", "owner_escalation"],
        "event_selectors": ["legal", "contract", "regulation"],
        "evidence_selectors": [
            "legal",
            "contract",
            "jurisdiction",
            "regulation",
            "privacy",
            "terms",
        ],
        "required_capabilities": ["legal_analysis"],
        "default_role_name": "Legal and Policy Agent",
        "risk_level": "high",
    },
    {
        "key": "sales",
        "display_name": "Sales",
        "purpose": "Develop and operate the evidence-backed commercial pipeline.",
        "inputs": ["erpnext.lead", "erpnext.opportunity", "customer_signal"],
        "outputs": ["pipeline_analysis", "lead_work", "approval_backed_outreach"],
        "event_selectors": ["erpnext.lead", "erpnext.opportunity"],
        "evidence_selectors": [
            "sales",
            "lead",
            "opportunity",
            "pipeline",
            "prospect",
            "client",
            "customer",
        ],
        "required_capabilities": ["pipeline_management"],
        "default_role_name": "Sales Agent",
        "risk_level": "medium",
    },
    {
        "key": "marketing",
        "display_name": "Marketing",
        "purpose": "Develop market evidence, positioning, and bounded experiments.",
        "inputs": ["market_evidence", "brand_signal", "experiment_result"],
        "outputs": ["market_hypothesis", "content_draft", "experiment_proposal"],
        "event_selectors": ["market", "brand", "experiment"],
        "evidence_selectors": [
            "marketing",
            "market",
            "brand",
            "campaign",
            "content",
            "acquisition",
            "public relations",
        ],
        "required_capabilities": ["market_research", "content_planning"],
        "default_role_name": "Marketing Agent",
        "risk_level": "medium",
    },
    {
        "key": "support",
        "display_name": "Support",
        "purpose": "Assess customer requests and prepare bounded resolutions.",
        "inputs": ["erpnext.issue", "email.received", "customer_signal"],
        "outputs": ["issue_assessment", "reply_draft", "escalation"],
        "event_selectors": ["erpnext.issue", "email.received", "customer_signal"],
        "evidence_selectors": [
            "support",
            "ticket",
            "issue",
            "complaint",
            "helpdesk",
            "customer service",
        ],
        "required_capabilities": ["customer_support"],
        "default_role_name": "Support Agent",
        "risk_level": "medium",
    },
    {
        "key": "product",
        "display_name": "Product and Project Management",
        "purpose": "Prioritize product and project work against company outcomes.",
        "inputs": ["erpnext.project", "erpnext.task", "customer_signal"],
        "outputs": ["prioritized_backlog", "project_update", "acceptance_assessment"],
        "event_selectors": ["erpnext.project", "erpnext.task"],
        "evidence_selectors": [
            "product",
            "project",
            "roadmap",
            "feature",
            "backlog",
            "delivery",
        ],
        "required_capabilities": ["product_management", "project_management"],
        "default_role_name": "Product and Project Agent",
    },
    {
        "key": "engineering",
        "display_name": "Engineering",
        "purpose": "Plan technical work and produce quality evidence or outsourcing requests.",
        "inputs": ["business_work_item", "workflow_failure", "quality_signal"],
        "outputs": ["technical_plan", "quality_evidence", "outsourcing_request"],
        "event_selectors": ["workflow_failure", "quality_signal"],
        "evidence_selectors": [
            "engineering",
            "software",
            "code",
            "quality",
            "release",
            "technical",
        ],
        "required_capabilities": ["software_engineering", "quality_assurance"],
        "default_role_name": "Engineering Agent",
    },
    {
        "key": "operations",
        "display_name": "Operations",
        "purpose": "Improve internal processes and coordinate governed procurement work.",
        "inputs": ["erpnext.material_request", "workflow_state", "readiness"],
        "outputs": ["operating_plan", "procurement_proposal", "process_improvement"],
        "event_selectors": ["erpnext.material_request", "workflow_state", "readiness"],
        "evidence_selectors": [
            "operations",
            "procurement",
            "supplier",
            "warehouse",
            "inventory",
            "logistics",
            "process",
        ],
        "required_capabilities": ["operations_management"],
        "default_role_name": "Operations Agent",
    },
    {
        "key": "hr",
        "display_name": "People and HR",
        "purpose": "Assess capacity, role needs, and people-operation requirements.",
        "inputs": ["role_gap", "workload_signal", "mandate_health"],
        "outputs": ["capacity_assessment", "role_proposal", "operating_guidance"],
        "event_selectors": ["role_gap", "workload_signal", "mandate_health"],
        "evidence_selectors": [
            "human resources",
            "hiring",
            "employee",
            "people",
            "payroll",
            "workforce",
            "capacity",
        ],
        "required_capabilities": ["workforce_planning"],
        "default_role_name": "People and HR Agent",
        "risk_level": "high",
    },
    {
        "key": "security",
        "display_name": "Security and Compliance",
        "purpose": "Detect security, privacy, and compliance risks and coordinate containment.",
        "inputs": ["audit_event", "auth_failure", "injection_quarantine"],
        "outputs": ["security_finding", "containment_plan", "owner_escalation"],
        "event_selectors": ["auth_failure", "injection_quarantine", "security"],
        "evidence_selectors": [
            "security",
            "authentication",
            "credential",
            "incident",
            "compliance",
            "gdpr",
            "soc2",
        ],
        "required_capabilities": ["security_review", "compliance_review"],
        "default_role_name": "Security and Compliance Agent",
        "core": True,
        "risk_level": "high",
    },
    {
        "key": "knowledge",
        "display_name": "Knowledge and Research",
        "purpose": "Acquire evidence, challenge claims, and maintain durable knowledge.",
        "inputs": ["document", "research", "memory", "company_claim"],
        "outputs": ["evidence_summary", "claim_challenge", "memory_update"],
        "event_selectors": [
            "document.updated",
            "website.snapshot",
            "research.results",
            "memory.entry",
        ],
        "evidence_selectors": [
            "research",
            "knowledge",
            "evidence",
            "document",
            "memory",
            "claim",
        ],
        "required_capabilities": ["research", "knowledge_management"],
        "default_role_name": "Knowledge and Research Agent",
        "core": True,
    },
    {
        "key": "communications",
        "display_name": "Communications",
        "purpose": "Prepare and deliver policy-authorized company communications.",
        "inputs": ["approved_communication", "owner_notification"],
        "outputs": ["communication_draft", "delivery_evidence"],
        "event_selectors": ["approved_communication", "owner_notification"],
        "evidence_selectors": [
            "communication",
            "email",
            "outreach",
            "notification",
            "message",
        ],
        "required_capabilities": ["business_communications"],
        "default_role_name": "Communications Agent",
        "risk_level": "high",
    },
    {
        "key": "supervisor",
        "display_name": "Supervisor",
        "purpose": "Coordinate outcomes, dependencies, escalations, and owner instructions.",
        "inputs": ["domain_health", "observer_finding", "owner_instruction"],
        "outputs": ["outcome_contract", "dependency_resolution", "owner_attention"],
        "event_selectors": ["owner.instruction", "observer_finding", "domain_health"],
        "evidence_selectors": [
            "objective",
            "dependency",
            "portfolio",
            "escalation",
            "management",
        ],
        "required_capabilities": ["portfolio_supervision"],
        "default_role_name": "Supervisor Agent",
        "core": True,
        "risk_level": "medium",
    },
    {
        "key": "governance",
        "display_name": "Governance",
        "purpose": "Review policy, decisions, evidence quality, and autonomous conduct.",
        "inputs": ["governor_decision", "policy_decision", "audit_event"],
        "outputs": ["observer_review", "policy_finding", "consensus_record"],
        "event_selectors": ["audit.event", "governor_decision", "policy_decision"],
        "evidence_selectors": [
            "governance",
            "policy",
            "audit",
            "observer",
            "approval",
            "risk",
        ],
        "required_capabilities": ["independent_review", "policy_governance"],
        "default_role_name": "Governance Agent",
        "core": True,
        "risk_level": "high",
    },
)
