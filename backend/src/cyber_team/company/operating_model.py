"""Adaptive company operating-model builder.

This module is intentionally deterministic. The LLM can enrich role design later, but
the bootstrap path must be explainable, testable, and safe enough to run in production.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


OPERATING_MODEL_VERSION = "autonomous-company-os-v2.0"


TOOL_ALIASES = {
    "call_make": "make_call",
    "email_send": "send_email",
    "message_send": "send_message",
    "memory_write": "memory_remember",
    "memory_read": "memory_recall",
    "sms_send": "send_sms",
    "crm_lead_create": "erpnext_create_lead",
    "erpnext_finance_read": "erpnext_get_invoices",
}


@dataclass(frozen=True)
class RoleDefinition:
    family: str
    name: str
    description: str
    instructions_template: str
    default_tools: list[str]
    approval_policy: str
    success_metrics: list[str]
    capabilities: list[str]
    base_priority: int


@dataclass(frozen=True)
class RoleSpec:
    family: str
    name: str
    description: str
    instructions_template: str
    default_tools: list[str]
    memory_namespace: str
    approval_policy: str
    success_metrics: list[str]
    capabilities: list[str]
    source: str
    priority: int
    rationale: list[str]
    needed_now: bool
    activation_triggers: list[str] = field(default_factory=list)
    supervisor_notes: list[str] = field(default_factory=list)
    manifest_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["manifest_payload"] = self.to_manifest_payload()
        return data

    def to_manifest_payload(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "name": self.name,
            "description": self.description,
            "instructions_template": self.instructions_template,
            "default_tools": self.default_tools,
            "memory_namespace": self.memory_namespace,
            "approval_policy": self.approval_policy,
            "success_metrics": self.success_metrics,
            "is_core": self.source != "dynamic",
            "config": {
                "source": self.source,
                "priority": self.priority,
                "rationale": self.rationale,
                "capabilities": self.capabilities,
                "needed_now": self.needed_now,
                "activation_triggers": self.activation_triggers,
                "supervisor_notes": self.supervisor_notes,
                "operating_model_version": OPERATING_MODEL_VERSION,
            },
        }


class OperatingModelBuilder:
    """Build a concrete, inspectable operating model from company context."""

    FOUNDATION_FAMILIES = {
        "company_builder",
        "supervisor",
        "knowledge",
        "operations",
        "communications",
    }

    FAMILY_SIGNALS = {
        "sales": {
            "client",
            "customer",
            "lead",
            "outreach",
            "revenue",
            "sales",
            "prospect",
            "b2b",
            "b2c",
            "crm",
        },
        "marketing": {
            "audience",
            "brand",
            "campaign",
            "content",
            "growth",
            "launch",
            "marketing",
            "pr",
            "social",
            "website",
        },
        "support": {
            "client",
            "customer",
            "helpdesk",
            "inbound",
            "service",
            "sla",
            "support",
            "ticket",
            "user",
        },
        "product": {
            "app",
            "feature",
            "platform",
            "product",
            "project",
            "roadmap",
            "saas",
            "software",
            "workflow",
        },
        "engineering": {
            "ai",
            "api",
            "app",
            "code",
            "engineering",
            "platform",
            "saas",
            "software",
            "technical",
        },
        "security": {
            "compliance",
            "data",
            "fintech",
            "health",
            "legal",
            "privacy",
            "regulated",
            "risk",
            "security",
        },
        "hr": {
            "employee",
            "employees",
            "hiring",
            "hr",
            "people",
            "recruiting",
            "team",
        },
        "finance": {
            "accounting",
            "billing",
            "cash",
            "finance",
            "invoice",
            "payment",
            "pricing",
            "revenue",
            "subscription",
        },
        "legal": {
            "contract",
            "jurisdiction",
            "legal",
            "nda",
            "policy",
            "privacy",
            "regulated",
            "terms",
        },
    }

    INTEGRATION_SIGNALS = {
        "accounting": {
            "signals": {"accounting", "invoice", "payment", "erp", "finance"},
            "tools": {"erpnext_get_invoices", "erpnext_create_lead"},
        },
        "calendar": {
            "signals": {"calendar", "schedule", "meeting", "appointment"},
            "tools": {"calendar_read", "calendar_event_create"},
        },
        "crm": {
            "signals": {"crm", "lead", "pipeline", "customer", "prospect"},
            "tools": {"erpnext_create_lead", "crm_lead_search"},
        },
        "documents": {
            "signals": {"document", "docs", "file", "knowledge", "nextcloud"},
            "tools": {"document_index", "knowledge_query"},
        },
        "email": {
            "signals": {"email", "inbox", "newsletter", "outreach"},
            "tools": {"send_email", "email_read"},
        },
        "messaging": {
            "signals": {"messenger", "slack", "telegram", "whatsapp", "chat"},
            "tools": {"send_message", "message_read"},
        },
        "phone_sms": {
            "signals": {"call", "client", "mobile", "phone", "sms", "voice"},
            "tools": {"make_call", "send_sms", "call_receive", "sms_read"},
        },
        "support": {
            "signals": {"helpdesk", "support", "ticket", "customer success"},
            "tools": {"ticket_read", "ticket_create", "ticket_update"},
        },
    }

    ROLE_DEFINITIONS = {
        "company_builder": RoleDefinition(
            family="company_builder",
            name="Company Builder",
            description="Designs and evolves the AI organization around company needs.",
            instructions_template=(
                "You are the Company Builder for {company_name}. Convert company context "
                "into roles, tools, memory policies, and adaptive operating loops. When a "
                "capability gap appears, propose or create the missing role through the "
                "role-gap workflow."
            ),
            default_tools=[
                "role_catalog_search",
                "role_instantiate",
                "company_profile_read",
                "memory_remember",
                "role_gap_report",
                "approval_request",
            ],
            approval_policy="auto",
            success_metrics=["role_coverage", "capability_gap_closure_time"],
            capabilities=["org_design", "role_factory", "capability_gap_detection"],
            base_priority=100,
        ),
        "supervisor": RoleDefinition(
            family="supervisor",
            name="Supervisor / Orchestrator",
            description="Coordinates agents, verifies work, and escalates risky decisions.",
            instructions_template=(
                "You are the Supervisor for {company_name}. Coordinate all specialist "
                "agents, inspect their plans before risky actions, enforce approval "
                "boundaries, and maintain a live view of company execution."
            ),
            default_tools=[
                "agent_status_read",
                "agent_invoke",
                "memory_recall",
                "role_gap_report",
                "approval_resolve",
                "owner_notify",
            ],
            approval_policy="auto",
            success_metrics=["blocked_risk_events", "escalation_latency"],
            capabilities=["orchestration", "quality_control", "risk_escalation"],
            base_priority=100,
        ),
        "knowledge": RoleDefinition(
            family="knowledge",
            name="Knowledge & Research Agent",
            description="Maintains research, documents, and retrieval-ready knowledge.",
            instructions_template=(
                "You are the Knowledge specialist for {company_name}. Keep company "
                "knowledge current, index important documents, and provide cited context "
                "to agents before they act."
            ),
            default_tools=["web_search", "document_index", "knowledge_query", "memory_remember"],
            approval_policy="auto",
            success_metrics=["knowledge_coverage", "retrieval_relevance"],
            capabilities=["research", "knowledge_management", "document_indexing"],
            base_priority=95,
        ),
        "operations": RoleDefinition(
            family="operations",
            name="Operations & Procurement Agent",
            description="Monitors operating cadence, vendors, SLAs, and process health.",
            instructions_template=(
                "You are the Operations specialist for {company_name}. Track process "
                "health, vendors, operational risks, and recurring business routines."
            ),
            default_tools=["sla_monitor", "process_audit", "vendor_search", "approval_request"],
            approval_policy="sensitive",
            success_metrics=["sla_compliance", "process_exception_rate"],
            capabilities=["operations_monitoring", "vendor_management", "process_design"],
            base_priority=88,
        ),
        "communications": RoleDefinition(
            family="communications",
            name="Communications Agent",
            description="Routes and manages external communication channels.",
            instructions_template=(
                "You are the Communications specialist for {company_name}. Route inbound "
                "and outbound communication across configured channels, preserve logs, "
                "and require approval for first-contact or high-impact messages."
            ),
            default_tools=[
                "send_email",
                "email_read",
                "send_sms",
                "sms_read",
                "make_call",
                "call_receive",
                "send_message",
                "message_read",
                "approval_request",
            ],
            approval_policy="sensitive",
            success_metrics=["response_time", "channel_coverage"],
            capabilities=["omnichannel_routing", "communication_logging"],
            base_priority=84,
        ),
        "finance": RoleDefinition(
            family="finance",
            name="Finance & Accounting Agent",
            description="Tracks invoices, cash flow, accounting records, and approvals.",
            instructions_template=(
                "You are the Finance specialist for {company_name}. Track invoices, "
                "expenses, cash flow, and accounting records. Never move money or modify "
                "financial records without the configured approval path."
            ),
            default_tools=[
                "erpnext_get_invoices",
                "erpnext_invoice_create",
                "erpnext_expense_track",
                "cashflow_forecast",
                "approval_request",
            ],
            approval_policy="sensitive",
            success_metrics=["invoice_accuracy", "forecast_deviation"],
            capabilities=["accounting", "cash_flow", "financial_controls"],
            base_priority=74,
        ),
        "legal": RoleDefinition(
            family="legal",
            name="Legal & Policy Agent",
            description="Drafts policies, contracts, and legal risk reviews.",
            instructions_template=(
                "You are the Legal specialist for {company_name}. Draft contracts and "
                "policies, identify legal risk, and escalate legal commitments for human "
                "review before execution."
            ),
            default_tools=["contract_draft", "policy_draft", "regulation_search", "approval_request"],
            approval_policy="sensitive",
            success_metrics=["policy_coverage", "legal_risk_turnaround"],
            capabilities=["contract_review", "policy_design", "legal_risk_triage"],
            base_priority=72,
        ),
        "sales": RoleDefinition(
            family="sales",
            name="Sales & CRM Agent",
            description="Manages leads, pipeline, outreach, and CRM updates.",
            instructions_template=(
                "You are the Sales specialist for {company_name}. Research leads, draft "
                "outreach, maintain CRM data, and coordinate approved follow-up."
            ),
            default_tools=[
                "crm_lead_search",
                "erpnext_create_lead",
                "send_email",
                "send_sms",
                "make_call",
                "approval_request",
            ],
            approval_policy="sensitive",
            success_metrics=["qualified_leads", "pipeline_value", "conversion_rate"],
            capabilities=["lead_generation", "pipeline_management", "approved_outreach"],
            base_priority=64,
        ),
        "marketing": RoleDefinition(
            family="marketing",
            name="Marketing & PR Agent",
            description="Creates content, campaigns, social drafts, and brand monitoring.",
            instructions_template=(
                "You are the Marketing specialist for {company_name}. Plan campaigns, "
                "draft content, monitor brand signals, and request approval before "
                "external publication."
            ),
            default_tools=["content_create", "social_post_draft", "brand_monitor", "analytics_read"],
            approval_policy="sensitive",
            success_metrics=["engagement_rate", "campaign_output", "brand_sentiment"],
            capabilities=["content_strategy", "public_relations", "growth_marketing"],
            base_priority=62,
        ),
        "support": RoleDefinition(
            family="support",
            name="Customer Support & Success Agent",
            description="Handles support tickets, customer success, and escalations.",
            instructions_template=(
                "You are the Customer Support specialist for {company_name}. Handle "
                "customer issues, update support records, and escalate unresolved or "
                "high-risk situations."
            ),
            default_tools=["ticket_read", "ticket_create", "ticket_update", "memory_remember"],
            approval_policy="auto",
            success_metrics=["response_time", "resolution_rate", "csat_score"],
            capabilities=["customer_support", "success_monitoring", "ticket_triage"],
            base_priority=60,
        ),
        "product": RoleDefinition(
            family="product",
            name="Product & Project Management Agent",
            description="Maintains roadmap, backlog, priorities, and delivery cadence.",
            instructions_template=(
                "You are the Product specialist for {company_name}. Maintain roadmap, "
                "prioritize work, create tasks, and coordinate delivery across agents."
            ),
            default_tools=["task_create", "task_update", "sprint_plan", "progress_report"],
            approval_policy="auto",
            success_metrics=["delivery_predictability", "backlog_quality"],
            capabilities=["roadmap_management", "project_management", "task_coordination"],
            base_priority=58,
        ),
        "engineering": RoleDefinition(
            family="engineering",
            name="Software Engineering & QA Agent",
            description="Inspects repositories, drafts changes, and coordinates QA.",
            instructions_template=(
                "You are the Engineering specialist for {company_name}. Inspect code, "
                "draft implementation plans, run tests, and never deploy or modify "
                "production without approval."
            ),
            default_tools=["git_read", "git_commit_draft", "test_run", "browser_automate"],
            approval_policy="sensitive",
            success_metrics=["test_pass_rate", "review_quality"],
            capabilities=["software_engineering", "quality_assurance", "release_support"],
            base_priority=56,
        ),
        "security": RoleDefinition(
            family="security",
            name="Security & Compliance Agent",
            description="Audits permissions, compliance, privacy, and incident signals.",
            instructions_template=(
                "You are the Security specialist for {company_name}. Monitor access, "
                "privacy, compliance, and incident signals. Escalate material risks "
                "to the owner immediately."
            ),
            default_tools=["security_scan", "access_audit", "compliance_check", "owner_notify"],
            approval_policy="auto",
            success_metrics=["risk_detection_rate", "incident_response_time"],
            capabilities=["security_monitoring", "compliance", "access_review"],
            base_priority=54,
        ),
        "hr": RoleDefinition(
            family="hr",
            name="People & HR Agent",
            description="Plans recruiting, onboarding, HR policies, and people operations.",
            instructions_template=(
                "You are the People specialist for {company_name}. Draft hiring and "
                "onboarding workflows, maintain HR policy context, and request approval "
                "for hiring or termination decisions."
            ),
            default_tools=["erpnext_hr_read", "job_posting_draft", "candidate_screen"],
            approval_policy="sensitive",
            success_metrics=["time_to_hire", "onboarding_completion"],
            capabilities=["recruiting", "onboarding", "people_operations"],
            base_priority=50,
        ),
    }

    def build(
        self,
        company_profile: dict[str, Any],
        existing_manifests: list[dict[str, Any]] | None = None,
        available_tools: set[str] | list[str] | None = None,
    ) -> dict[str, Any]:
        manifests = existing_manifests or []
        tool_names = set(available_tools or [])
        normalized = self._normalize_profile(company_profile)
        company_name = normalized["company_name"]
        company_namespace = f"company:{_slug(company_name)}"
        manifest_by_family = self._manifest_by_family(manifests)

        specs = []
        for family in self.ROLE_DEFINITIONS:
            score, rationale, triggers = self._score_family(family, normalized)
            if score <= 0 and family not in self.FOUNDATION_FAMILIES:
                continue
            needed_now = family in self.FOUNDATION_FAMILIES or score >= 60
            spec = self._make_role_spec(
                family=family,
                manifest=manifest_by_family.get(family),
                company_namespace=company_namespace,
                score=score,
                rationale=rationale,
                triggers=triggers,
                needed_now=needed_now,
            )
            specs.append(spec)

        specs.extend(self._dynamic_role_specs(normalized, company_namespace))
        specs = self._dedupe_specs(specs)
        specs = sorted(specs, key=lambda spec: (-spec.priority, spec.name))

        planned_specs = [spec for spec in specs if spec.needed_now]
        deferred_specs = [spec for spec in specs if not spec.needed_now]
        capability_gaps = self._capability_gaps(planned_specs, normalized, tool_names)
        adaptive_loops = self._adaptive_loops(planned_specs, normalized)
        memory_seed = self._memory_seed(
            company_name=company_name,
            company_namespace=company_namespace,
            planned_specs=planned_specs,
            capability_gaps=capability_gaps,
            adaptive_loops=adaptive_loops,
        )

        return {
            "version": OPERATING_MODEL_VERSION,
            "company_name": company_name,
            "company_namespace": company_namespace,
            "decision_basis": normalized["decision_basis"],
            "summary": {
                "planned_role_count": len(planned_specs),
                "deferred_role_count": len(deferred_specs),
                "dynamic_role_count": len([spec for spec in specs if spec.source == "dynamic"]),
                "capability_gap_count": len(capability_gaps),
            },
            "role_specs": [spec.to_dict() for spec in specs],
            "planned_role_specs": [spec.to_dict() for spec in planned_specs],
            "role_backlog": [spec.to_dict() for spec in deferred_specs],
            "capability_map": self._capability_map(planned_specs),
            "capability_gaps": capability_gaps,
            "adaptive_loops": adaptive_loops,
            "memory_seed": memory_seed,
            "recommended_next_questions": self._next_questions(normalized, capability_gaps),
        }

    def _make_role_spec(
        self,
        family: str,
        manifest: dict[str, Any] | None,
        company_namespace: str,
        score: int,
        rationale: list[str],
        triggers: list[str],
        needed_now: bool,
    ) -> RoleSpec:
        definition = self.ROLE_DEFINITIONS[family]
        if manifest:
            return RoleSpec(
                family=manifest["family"],
                name=manifest["name"],
                description=manifest["description"],
                instructions_template=manifest["instructions_template"],
                default_tools=list(manifest["default_tools"]),
                memory_namespace=manifest["memory_namespace"],
                approval_policy=manifest["approval_policy"],
                success_metrics=list(manifest["success_metrics"] or []),
                capabilities=definition.capabilities,
                source="catalog",
                priority=score,
                rationale=rationale,
                needed_now=needed_now,
                activation_triggers=triggers,
                manifest_id=manifest["id"],
            )
        return RoleSpec(
            family=definition.family,
            name=definition.name,
            description=definition.description,
            instructions_template=definition.instructions_template,
            default_tools=definition.default_tools,
            memory_namespace=f"{company_namespace}:{definition.family}",
            approval_policy=definition.approval_policy,
            success_metrics=definition.success_metrics,
            capabilities=definition.capabilities,
            source="generated_seed",
            priority=score,
            rationale=rationale,
            needed_now=needed_now,
            activation_triggers=triggers,
        )

    def _dynamic_role_specs(
        self,
        normalized: dict[str, Any],
        company_namespace: str,
    ) -> list[RoleSpec]:
        terms = normalized["terms"]
        specs = [
            RoleSpec(
                family="knowledge",
                name="Company Memory Steward",
                description=(
                    "Owns memory hygiene, retrieval policy, consolidation cadence, and "
                    "the illusion of long-term recall for every agent."
                ),
                instructions_template=(
                    "You are the Company Memory Steward for {company_name}. Maintain "
                    "company-wide memory protocol, decide what must be remembered, "
                    "consolidate duplicated or stale facts, and teach agents which "
                    "memory namespaces to query before acting."
                ),
                default_tools=[
                    "memory_recall",
                    "memory_remember",
                    "document_index",
                    "knowledge_query",
                    "owner_notify",
                ],
                memory_namespace=f"{company_namespace}:memory_steward",
                approval_policy="auto",
                success_metrics=["memory_recall_precision", "memory_freshness"],
                capabilities=["memory_governance", "retrieval_policy", "memory_consolidation"],
                source="dynamic",
                priority=96,
                rationale=["Persistent company memory is a foundation requirement."],
                needed_now=True,
                activation_triggers=["company_bootstrap", "memory_gap_detected"],
                supervisor_notes=[
                    "Consult this role when an agent lacks context or conflicting facts appear.",
                ],
            )
        ]

        if terms & self.INTEGRATION_SIGNALS["phone_sms"]["signals"]:
            specs.append(
                RoleSpec(
                    family="communications",
                    name="Outbound Calling Specialist",
                    description=(
                        "Handles approved outbound calls, SMS follow-up, and call summaries "
                        "for customer or partner communication."
                    ),
                    instructions_template=(
                        "You are the Outbound Calling Specialist for {company_name}. Prepare "
                        "call scripts, request approval before first contact, place approved "
                        "calls or SMS messages through configured providers, and write concise "
                        "post-contact memory entries."
                    ),
                    default_tools=[
                        "make_call",
                        "send_sms",
                        "send_email",
                        "memory_recall",
                        "memory_remember",
                        "approval_request",
                        "owner_notify",
                    ],
                    memory_namespace=f"{company_namespace}:outbound_calling",
                    approval_policy="sensitive",
                    success_metrics=["approved_contacts_completed", "follow_up_accuracy"],
                    capabilities=["outbound_voice", "sms_follow_up", "call_summarization"],
                    source="dynamic",
                    priority=86,
                    rationale=["The company profile mentions phone, SMS, mobile, or clients."],
                    needed_now=True,
                    activation_triggers=sorted(
                        terms & self.INTEGRATION_SIGNALS["phone_sms"]["signals"]
                    ),
                    supervisor_notes=[
                        "First-contact outreach and material promises require owner approval.",
                    ],
                )
            )

        integration_matches = self._matched_integrations(normalized)
        if len(integration_matches) >= 2 or "integration" in terms or "integrations" in terms:
            specs.append(
                RoleSpec(
                    family="operations",
                    name="Integration Architect",
                    description=(
                        "Discovers required business systems, maps tool gaps, and proposes "
                        "safe connector activation order."
                    ),
                    instructions_template=(
                        "You are the Integration Architect for {company_name}. Inspect the "
                        "company profile, detect which external systems are required, map "
                        "available tools against missing connectors, and propose integration "
                        "work in risk-ranked order."
                    ),
                    default_tools=[
                        "company_profile_read",
                        "memory_recall",
                        "memory_remember",
                        "process_audit",
                        "owner_notify",
                        "approval_request",
                    ],
                    memory_namespace=f"{company_namespace}:integration_architect",
                    approval_policy="auto",
                    success_metrics=["integration_gap_closure", "connector_readiness"],
                    capabilities=["integration_discovery", "tool_gap_mapping"],
                    source="dynamic",
                    priority=82,
                    rationale=["Multiple external business systems appear in the company profile."],
                    needed_now=True,
                    activation_triggers=integration_matches,
                    supervisor_notes=[
                        "Connector credentials and live write actions remain approval-gated.",
                    ],
                )
            )

        regulated_terms = terms & self.FAMILY_SIGNALS["security"]
        if regulated_terms:
            specs.append(
                RoleSpec(
                    family="security",
                    name="Compliance Sentinel",
                    description="Continuously reviews privacy, compliance, and policy obligations.",
                    instructions_template=(
                        "You are the Compliance Sentinel for {company_name}. Maintain the "
                        "compliance watchlist, inspect risky plans before execution, and "
                        "escalate any legal, privacy, or regulated-market concern."
                    ),
                    default_tools=[
                        "compliance_check",
                        "regulation_search",
                        "access_audit",
                        "memory_recall",
                        "owner_notify",
                    ],
                    memory_namespace=f"{company_namespace}:compliance_sentinel",
                    approval_policy="auto",
                    success_metrics=["risk_reviews_completed", "missed_obligation_count"],
                    capabilities=["compliance_monitoring", "policy_watchlist"],
                    source="dynamic",
                    priority=80,
                    rationale=["The company profile contains regulated, privacy, or security terms."],
                    needed_now=True,
                    activation_triggers=sorted(regulated_terms),
                    supervisor_notes=["Review external communications and contracts before release."],
                )
            )

        growth_terms = terms & {"growth", "launch", "campaign", "audience", "marketing", "sales"}
        if growth_terms:
            specs.append(
                RoleSpec(
                    family="marketing",
                    name="Growth Experiment Designer",
                    description="Designs small, measurable growth experiments for the company.",
                    instructions_template=(
                        "You are the Growth Experiment Designer for {company_name}. Convert "
                        "business goals into small experiments, define success metrics, "
                        "draft assets, and coordinate with sales, marketing, and analytics."
                    ),
                    default_tools=[
                        "content_create",
                        "analytics_read",
                        "brand_monitor",
                        "memory_recall",
                        "owner_notify",
                    ],
                    memory_namespace=f"{company_namespace}:growth_experiments",
                    approval_policy="sensitive",
                    success_metrics=["experiments_shipped", "validated_learning_rate"],
                    capabilities=["growth_experimentation", "metric_design"],
                    source="dynamic",
                    priority=70,
                    rationale=["The company profile indicates launch, marketing, or growth work."],
                    needed_now=True,
                    activation_triggers=sorted(growth_terms),
                    supervisor_notes=["External campaign publication requires approval."],
                )
            )

        return specs

    def _score_family(
        self,
        family: str,
        normalized: dict[str, Any],
    ) -> tuple[int, list[str], list[str]]:
        definition = self.ROLE_DEFINITIONS[family]
        terms = normalized["terms"]
        rationale = []
        triggers = []

        if family in self.FOUNDATION_FAMILIES:
            rationale.append("Foundation role required for autonomous company operation.")
            return definition.base_priority, rationale, ["company_bootstrap"]

        score = 0
        signal_matches = terms & self.FAMILY_SIGNALS.get(family, set())
        if signal_matches:
            triggers.extend(sorted(signal_matches))
            score += min(24, 8 * len(signal_matches))
            rationale.append(f"Matched profile signals: {', '.join(sorted(signal_matches))}.")

        if family in {"finance", "legal"}:
            score += 50
            rationale.append("Every operating business needs baseline finance and legal coverage.")

        if family in {"sales", "marketing", "support"} and terms & {"client", "customer", "user"}:
            score += 18
            rationale.append("Customer-facing language indicates go-to-market or support work.")

        if family in {"product", "engineering"} and terms & {"ai", "app", "platform", "saas"}:
            score += 22
            rationale.append("Digital product language indicates product and engineering work.")

        if family == "security" and terms & {"ai", "data", "saas", "software"}:
            score += 12
            rationale.append("Digital systems and data require baseline security review.")

        if family == "hr" and terms & {"team", "hiring", "employees"}:
            score += 28
            rationale.append("People operations signals are present.")

        if not rationale:
            rationale.append("Candidate role deferred until company context creates demand.")
            return min(59, definition.base_priority), rationale, triggers

        priority = min(99, definition.base_priority + score)
        return priority, rationale, triggers

    def _normalize_profile(self, company_profile: dict[str, Any]) -> dict[str, Any]:
        company_name = (
            company_profile.get("name")
            or company_profile.get("company_name")
            or company_profile.get("company")
            or "Cyber-Team Company"
        )
        text = _flatten_text(company_profile)
        terms = set(re.findall(r"[a-z0-9][a-z0-9_+-]*", text.lower()))
        decision_basis = {
            "provided_fields": sorted(str(key) for key in company_profile),
            "detected_terms": sorted(terms)[:120],
            "profile_completeness": self._profile_completeness(company_profile),
        }
        return {
            "company_name": str(company_name),
            "raw_profile": company_profile,
            "text": text,
            "terms": terms,
            "decision_basis": decision_basis,
        }

    def _capability_gaps(
        self,
        planned_specs: list[RoleSpec],
        normalized: dict[str, Any],
        available_tools: set[str],
    ) -> list[dict[str, Any]]:
        gaps = []
        if available_tools:
            missing_by_role = {}
            for spec in planned_specs:
                missing = [
                    tool
                    for tool in spec.default_tools
                    if _canonical_tool(tool) not in available_tools
                ]
                if missing:
                    missing_by_role[spec.name] = missing
            for role_name, missing in missing_by_role.items():
                gaps.append(
                    {
                        "type": "missing_tool",
                        "role_name": role_name,
                        "missing_tools": missing,
                        "severity": "medium",
                        "recommended_action": "Implement or map these tools before live execution.",
                    }
                )

        for integration in self._matched_integrations(normalized):
            desired_tools = self.INTEGRATION_SIGNALS[integration]["tools"]
            missing_tools = sorted(tool for tool in desired_tools if tool not in available_tools)
            if missing_tools and available_tools:
                gaps.append(
                    {
                        "type": "integration_gap",
                        "integration": integration,
                        "missing_tools": missing_tools,
                        "severity": "high" if integration in {"phone_sms", "email"} else "medium",
                        "recommended_action": (
                            "Assign Integration Architect to validate credentials, provider "
                            "status, and connector implementation."
                        ),
                    }
                )
        return gaps

    def _adaptive_loops(
        self,
        planned_specs: list[RoleSpec],
        normalized: dict[str, Any],
    ) -> list[dict[str, Any]]:
        role_families = {spec.family for spec in planned_specs}
        loops = [
            {
                "id": "owner_alignment_loop",
                "owner_family": "supervisor",
                "purpose": "Keep company execution aligned with owner intent.",
                "trigger": "new owner instruction, major plan, or weekly review",
                "approval_boundary": "Owner confirms strategic changes and high-risk actions.",
            },
            {
                "id": "role_gap_monitoring_loop",
                "owner_family": "company_builder",
                "purpose": "Detect missing roles, skills, tools, or permissions during operation.",
                "trigger": "agent reports blocked work or repeated unsupported tool requests",
                "approval_boundary": "Dynamic role creation is logged and sensitive tools stay gated.",
            },
            {
                "id": "memory_consolidation_loop",
                "owner_family": "knowledge",
                "purpose": "Maintain long-term company memory and resolve stale or conflicting facts.",
                "trigger": "new project, completed workflow, contradictory memory, or daily cadence",
                "approval_boundary": "Deleting or overwriting canonical records requires review.",
            },
            {
                "id": "integration_discovery_loop",
                "owner_family": "operations",
                "purpose": "Find required external systems and map them to safe tool contracts.",
                "trigger": "profile mentions new system or agent requests unavailable connector",
                "approval_boundary": "Credentials and live external writes require owner approval.",
            },
            {
                "id": "risk_review_loop",
                "owner_family": "supervisor",
                "purpose": "Review financial, legal, public, production, and personal-data actions.",
                "trigger": "sensitive tool call, external publication, payment, or contract action",
                "approval_boundary": "Sensitive and irreversible actions require approval.",
            },
        ]
        if role_families & {"sales", "support", "communications"} or (
            normalized["terms"] & self.INTEGRATION_SIGNALS["phone_sms"]["signals"]
        ):
            loops.append(
                {
                    "id": "customer_communication_loop",
                    "owner_family": "communications",
                    "purpose": "Route inbound/outbound customer messages and preserve summaries.",
                    "trigger": "incoming message, approved outreach, missed call, or stale lead",
                    "approval_boundary": "First-contact outreach and commitments require approval.",
                }
            )
        return loops

    def _memory_seed(
        self,
        company_name: str,
        company_namespace: str,
        planned_specs: list[RoleSpec],
        capability_gaps: list[dict[str, Any]],
        adaptive_loops: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        role_lines = [f"- {spec.name}: {', '.join(spec.capabilities)}" for spec in planned_specs]
        loop_lines = [f"- {loop['id']}: {loop['purpose']}" for loop in adaptive_loops]
        gap_lines = [
            f"- {gap['type']}: {gap.get('role_name') or gap.get('integration')}"
            for gap in capability_gaps
        ] or ["- No immediate capability gaps detected."]
        return [
            {
                "id": "company_constitution",
                "memory_type": "semantic",
                "namespace": company_namespace,
                "importance": 0.95,
                "content": (
                    f"{company_name} operates through adaptive AI roles. Agents must query "
                    "company memory before material actions, respect approval boundaries, "
                    "and escalate uncertainty to the Supervisor or owner."
                ),
            },
            {
                "id": "initial_role_map",
                "memory_type": "semantic",
                "namespace": f"{company_namespace}:roles",
                "importance": 0.9,
                "content": "Initial planned roles:\n" + "\n".join(role_lines),
            },
            {
                "id": "adaptive_operating_loops",
                "memory_type": "procedural",
                "namespace": f"{company_namespace}:operations",
                "importance": 0.85,
                "content": "Adaptive operating loops:\n" + "\n".join(loop_lines),
            },
            {
                "id": "capability_gap_backlog",
                "memory_type": "procedural",
                "namespace": f"{company_namespace}:gaps",
                "importance": 0.8,
                "content": "Current capability gaps:\n" + "\n".join(gap_lines),
            },
        ]

    def _capability_map(self, specs: list[RoleSpec]) -> dict[str, dict[str, Any]]:
        return {
            spec.name: {
                "family": spec.family,
                "capabilities": spec.capabilities,
                "tools": spec.default_tools,
                "approval_policy": spec.approval_policy,
                "memory_namespace": spec.memory_namespace,
            }
            for spec in specs
        }

    def _next_questions(
        self,
        normalized: dict[str, Any],
        capability_gaps: list[dict[str, Any]],
    ) -> list[str]:
        questions = []
        completeness = normalized["decision_basis"]["profile_completeness"]
        if completeness < 0.45:
            questions.append("What are the company's main products, customers, and channels?")
        if "jurisdiction" not in normalized["terms"] and "country" not in normalized["terms"]:
            questions.append("Which jurisdictions, countries, or regulatory regimes matter?")
        if capability_gaps:
            questions.append("Which missing integrations should be connected first?")
        if "goal" not in normalized["terms"] and "goals" not in normalized["terms"]:
            questions.append("What business outcomes should the AI team optimize for first?")
        return questions[:4]

    def _manifest_by_family(self, manifests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        manifest_by_family = {}
        for manifest in manifests:
            family = manifest.get("family")
            if not family or not manifest.get("is_core", True):
                continue
            if family not in manifest_by_family or manifest.get("is_core", False):
                manifest_by_family[family] = manifest
        return manifest_by_family

    def _matched_integrations(self, normalized: dict[str, Any]) -> list[str]:
        terms = normalized["terms"]
        matches = []
        for name, definition in self.INTEGRATION_SIGNALS.items():
            signals = definition["signals"]
            if terms & signals or any(signal in normalized["text"].lower() for signal in signals):
                matches.append(name)
        return sorted(matches)

    def _profile_completeness(self, company_profile: dict[str, Any]) -> float:
        important_fields = {
            "business_model",
            "channels",
            "goals",
            "industry",
            "jurisdictions",
            "name",
            "product",
            "stage",
            "target_customers",
        }
        present = {
            key
            for key in important_fields
            if _has_value(company_profile.get(key))
        }
        return round(len(present) / len(important_fields), 2)

    def _dedupe_specs(self, specs: list[RoleSpec]) -> list[RoleSpec]:
        deduped = {}
        for spec in specs:
            deduped[_slug(spec.name)] = spec
        return list(deduped.values())


def _canonical_tool(tool_name: str) -> str:
    return TOOL_ALIASES.get(tool_name, tool_name)


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            parts.append(str(key))
            parts.append(_flatten_text(item))
        return " ".join(parts)
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _has_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (dict, list, set, tuple)) and not value:
        return False
    return True


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "company"
