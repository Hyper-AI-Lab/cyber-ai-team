"""SQLAlchemy models for Cyber-Team."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cyber_team.clock import utc_now
from cyber_team.db import Base


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role_family: Mapped[str] = mapped_column(String(100), index=True)
    role_name: Mapped[str] = mapped_column(String(200))
    instructions: Mapped[str] = mapped_column(Text)
    tools: Mapped[list] = mapped_column(JSON, default=list)
    memory_namespace: Mapped[str] = mapped_column(String(200))
    approval_policy: Mapped[str] = mapped_column(String(50), default="auto")
    status: Mapped[str] = mapped_column(String(20), default="active")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class AgentCapabilityGrant(Base):
    __tablename__ = "agent_capability_grants"
    __table_args__ = (
        UniqueConstraint(
            "agent_id",
            "tool_name",
            name="uq_agent_capability_grants_agent_tool",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), ForeignKey("agents.id"), index=True)
    role_gap_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("role_gaps.id"),
        nullable=True,
        index=True,
    )
    tool_name: Mapped[str] = mapped_column(String(100), index=True)
    state: Mapped[str] = mapped_column(String(30), default="active", index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="low", index=True)
    side_effects: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("approval_requests.id"),
        nullable=True,
        index=True,
    )
    requested_by: Mapped[str] = mapped_column(String(200), default="system")
    reason: Mapped[str] = mapped_column(Text, default="")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    graph_definition: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    trigger_type: Mapped[str] = mapped_column(String(30), default="manual")
    trigger_config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
    )


class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"
    __table_args__ = (
        UniqueConstraint(
            "name",
            "version",
            name="uq_workflow_templates_name_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(40), default="1.0.0", index=True)
    graph_definition: Mapped[dict] = mapped_column(JSON, default=dict)
    default_trigger_type: Mapped[str] = mapped_column(String(30), default="manual")
    default_trigger_config: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    is_core: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(64), ForeignKey("workflows.id"))
    status: Mapped[str] = mapped_column(String(20), default="running")
    current_node: Mapped[str | None] = mapped_column(String(200), nullable=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    workflow: Mapped["Workflow"] = relationship(back_populates="runs")


class MemoryEntry(Base):
    __tablename__ = "memory_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("agents.id"), nullable=True)
    memory_type: Mapped[str] = mapped_column(String(30), index=True)
    namespace: Mapped[str] = mapped_column(String(200), index=True)
    content: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MemoryTrace(Base):
    __tablename__ = "memory_traces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    invocation_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(50), default="agent_invocation", index=True)
    task_excerpt: Mapped[str] = mapped_column(Text)
    memory_namespace: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    read_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    write_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    recalled_memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    written_memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    recall_count: Mapped[int] = mapped_column(Integer, default=0)
    write_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class MemoryStewardFinding(Base):
    __tablename__ = "memory_steward_findings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    finding_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium", index=True)
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    memory_namespace: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    company_namespace: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(Text)
    trace_ids: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_type: Mapped[str] = mapped_column(String(100))
    action_description: Mapped[str] = mapped_column(Text)
    action_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    requester: Mapped[str] = mapped_column(String(200), default="system")
    requester_type: Mapped[str] = mapped_column(String(30), default="system")
    risk_level: Mapped[str] = mapped_column(String(20), default="medium")
    target_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    reviewer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    actor: Mapped[str] = mapped_column(String(200), index=True)
    actor_type: Mapped[str] = mapped_column(String(30), default="system")
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    action: Mapped[str | None] = mapped_column(String(100), nullable=True)
    outcome: Mapped[str] = mapped_column(String(30), default="success", index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class CommunicationLog(Base):
    __tablename__ = "communication_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("agents.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(30))
    direction: Mapped[str] = mapped_column(String(10))
    recipient: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="sent")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class InboundEmailMessage(Base):
    __tablename__ = "inbound_email_messages"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "mailbox",
            "provider_uid",
            name="uq_inbound_email_provider_mailbox_uid",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), default="imap", index=True)
    mailbox: Mapped[str] = mapped_column(String(200), index=True)
    provider_uid: Mapped[str] = mapped_column(String(200))
    message_id: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    from_address: Mapped[str] = mapped_column(String(500), index=True)
    to_addresses: Mapped[list] = mapped_column(JSON, default=list)
    cc_addresses: Mapped[list] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(String(500), default="")
    text_body: Mapped[str] = mapped_column(Text, default="")
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    snippet: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="new", index=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
    )
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class RoleManifest(Base):
    __tablename__ = "role_manifests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    family: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text)
    instructions_template: Mapped[str] = mapped_column(Text)
    default_tools: Mapped[list] = mapped_column(JSON, default=list)
    memory_namespace: Mapped[str] = mapped_column(String(200))
    approval_policy: Mapped[str] = mapped_column(String(50), default="auto")
    success_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    is_core: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class RoleGap(Base):
    __tablename__ = "role_gaps"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium", index=True)
    source_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(30), default="agent")
    company_namespace: Mapped[str] = mapped_column(
        String(200),
        default="company:default",
        index=True,
    )
    capability: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    requested_tools: Mapped[list] = mapped_column(JSON, default=list)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    proposed_role: Mapped[dict] = mapped_column(JSON, default=dict)
    resolution: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CompanyContextSnapshot(Base):
    __tablename__ = "company_context_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "source_hash",
            name="uq_company_context_snapshots_source_hash",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(40), default="erpnext", index=True)
    source_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    company_namespace: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    normalized_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    erpnext_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    operating_model: Mapped[dict] = mapped_column(JSON, default=dict)
    memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    agent_ids: Mapped[list] = mapped_column(JSON, default=list)
    role_manifest_ids: Mapped[list] = mapped_column(JSON, default=list)
    role_gap_ids: Mapped[list] = mapped_column(JSON, default=list)
    approval_ids: Mapped[list] = mapped_column(JSON, default=list)
    plan_ids: Mapped[list] = mapped_column(JSON, default=list)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(200), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CompanyContextSyncRun(Base):
    __tablename__ = "company_context_sync_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(40), default="erpnext", index=True)
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    apply_low_risk: Mapped[bool] = mapped_column(Boolean, default=True)
    run_planner: Mapped[bool] = mapped_column(Boolean, default=True)
    snapshot_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("company_context_snapshots.id"),
        nullable=True,
        index=True,
    )
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    company_namespace: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    actor: Mapped[str] = mapped_column(String(200), default="system")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TeamActivationRun(Base):
    __tablename__ = "team_activation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_snapshot_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("company_context_snapshots.id"),
        nullable=True,
        index=True,
    )
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    company_namespace: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    apply_safe_roles: Mapped[bool] = mapped_column(Boolean, default=True)
    request_high_risk_grants: Mapped[bool] = mapped_column(Boolean, default=True)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    actor: Mapped[str] = mapped_column(String(200), default="system")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OrchestrationGovernorRun(Base):
    __tablename__ = "orchestration_governor_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    actor: Mapped[str] = mapped_column(String(200), default="chief_operating_agent")
    policy_version: Mapped[str] = mapped_column(String(80), default="governor-v1")
    mode: Mapped[str] = mapped_column(String(40), default="manual")
    auto_apply_low_risk: Mapped[bool] = mapped_column(Boolean, default=True)
    max_actions: Mapped[int] = mapped_column(Integer, default=10)
    snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    operating_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    operating_brief: Mapped[str] = mapped_column(Text, default="")
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    decisions: Mapped[list["OrchestrationGovernorDecision"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class OrchestrationGovernorDecision(Base):
    __tablename__ = "orchestration_governor_decisions"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_orchestration_governor_decisions_idempotency_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("orchestration_governor_runs.id"),
        index=True,
    )
    decision_type: Mapped[str] = mapped_column(String(60), index=True)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="low", index=True)
    source_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    target_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    target_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    action_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("approval_requests.id"),
        nullable=True,
        index=True,
    )
    plan_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("autonomous_plans.id"),
        nullable=True,
        index=True,
    )
    tool_proposal_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("orchestration_tool_proposals.id"),
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    run: Mapped["OrchestrationGovernorRun"] = relationship(back_populates="decisions")


class OrchestrationToolProposal(Base):
    __tablename__ = "orchestration_tool_proposals"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_orchestration_tool_proposals_idempotency_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    capability: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="medium", index=True)
    side_effects: Mapped[bool] = mapped_column(Boolean, default=False)
    source_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    purpose: Mapped[str] = mapped_column(Text)
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    required_credentials: Mapped[list] = mapped_column(JSON, default=list)
    executor_kind: Mapped[str] = mapped_column(String(60), default="proposed_executor")
    tests_required: Mapped[list] = mapped_column(JSON, default=list)
    rollback_notes: Mapped[str] = mapped_column(Text, default="")
    readiness_checks: Mapped[list] = mapped_column(JSON, default=list)
    sandbox_mode: Mapped[str] = mapped_column(String(40), default="proposal_only")
    sandbox_result: Mapped[dict] = mapped_column(JSON, default=dict)
    approval_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("approval_requests.id"),
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), index=True)
    created_by: Mapped[str] = mapped_column(String(200), default="chief_operating_agent")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class AutonomyPolicy(Base):
    __tablename__ = "autonomy_policies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(60), default="aggressive_threshold", index=True)
    resource_policy: Mapped[str] = mapped_column(String(60), default="foss_only", index=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    thresholds: Mapped[dict] = mapped_column(JSON, default=dict)
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_by: Mapped[str] = mapped_column(String(200), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class CompanyObjective(Base):
    __tablename__ = "company_objectives"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="medium", index=True)
    target: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(200), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class OperatingKPIDefinition(Base):
    __tablename__ = "operating_kpi_definitions"
    __table_args__ = (
        UniqueConstraint("key", name="uq_operating_kpi_definitions_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str] = mapped_column(String(40), default="count")
    comparison: Mapped[str] = mapped_column(String(20), default="max")
    target_value: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(100), default="governor_snapshot")
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class OperatingKPIObservation(Base):
    __tablename__ = "operating_kpi_observations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kpi_key: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(30), default="recorded", index=True)
    source_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class ExecutiveBenchmarkDefinition(Base):
    __tablename__ = "executive_benchmark_definitions"
    __table_args__ = (
        UniqueConstraint("key", name="uq_executive_benchmark_definitions_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default="")
    kpi_keys: Mapped[list] = mapped_column(JSON, default=list)
    rule: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(20), default="medium", index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    created_by: Mapped[str] = mapped_column(String(200), default="chief_operating_agent")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class ExecutiveBenchmarkResult(Base):
    __tablename__ = "executive_benchmark_results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    benchmark_key: Mapped[str] = mapped_column(String(120), index=True)
    run_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("orchestration_governor_runs.id"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(30), default="passed", index=True)
    score: Mapped[float] = mapped_column(Float, default=1.0)
    observed_value: Mapped[float] = mapped_column(Float, default=0.0)
    threshold_value: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class OperationGraphNode(Base):
    __tablename__ = "operation_graph_nodes"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_operation_graph_nodes_idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_type: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(240))
    summary: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="low", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    impact_score: Mapped[float] = mapped_column(Float, default=0.0)
    memory_namespace: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(240), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class OperationGraphEdge(Base):
    __tablename__ = "operation_graph_edges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_node_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("operation_graph_nodes.id"),
        index=True,
    )
    target_node_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("operation_graph_nodes.id"),
        index=True,
    )
    edge_type: Mapped[str] = mapped_column(String(80), default="related_to", index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class ExecutiveReflection(Base):
    __tablename__ = "executive_reflections"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("orchestration_governor_runs.id"),
        nullable=True,
        index=True,
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    what_changed: Mapped[list] = mapped_column(JSON, default=list)
    repeated_patterns: Mapped[list] = mapped_column(JSON, default=list)
    failures: Mapped[list] = mapped_column(JSON, default=list)
    memory_gaps: Mapped[list] = mapped_column(JSON, default=list)
    next_watch_items: Mapped[list] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class ObserverReview(Base):
    __tablename__ = "observer_reviews"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("orchestration_governor_runs.id"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(30), default="agreed", index=True)
    critique: Mapped[str] = mapped_column(Text, default="")
    findings: Mapped[list] = mapped_column(JSON, default=list)
    consensus_log: Mapped[list] = mapped_column(JSON, default=list)
    unresolved_objections: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class AutonomousExecutionRecord(Base):
    __tablename__ = "autonomous_execution_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_autonomous_execution_records_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("orchestration_governor_runs.id"),
        nullable=True,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(30), default="planned", index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="low", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    impact: Mapped[dict] = mapped_column(JSON, default=dict)
    approval_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("approval_requests.id"),
        nullable=True,
        index=True,
    )
    operation_node_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("operation_graph_nodes.id"),
        nullable=True,
        index=True,
    )
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(240), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OutsourcingRequest(Base):
    __tablename__ = "outsourcing_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    complexity_reason: Mapped[str] = mapped_column(Text, default="")
    task_spec: Mapped[dict] = mapped_column(JSON, default=dict)
    context_pack: Mapped[dict] = mapped_column(JSON, default=dict)
    acceptance_tests: Mapped[list] = mapped_column(JSON, default=list)
    foss_constraints: Mapped[list] = mapped_column(JSON, default=list)
    security_constraints: Mapped[list] = mapped_column(JSON, default=list)
    files_involved: Mapped[list] = mapped_column(JSON, default=list)
    expected_artifact: Mapped[str] = mapped_column(Text, default="")
    replay_instructions: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    approval_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("approval_requests.id"),
        nullable=True,
        index=True,
    )
    created_by: Mapped[str] = mapped_column(String(200), default="chief_operating_agent")
    resolution: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AutonomousPlan(Base):
    __tablename__ = "autonomous_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    objective: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(80), index=True)
    source_id: Mapped[str] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(30), default="planned", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="medium", index=True)
    created_by: Mapped[str] = mapped_column(String(200), default="autonomous_planner")
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    tasks: Mapped[list["AutonomousTask"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
    )


class AutonomousTask(Base):
    __tablename__ = "autonomous_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("autonomous_plans.id"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(30), default="planned", index=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    target_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    target_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    action_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    autonomous_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="low", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    plan: Mapped["AutonomousPlan"] = relationship(back_populates="tasks")
