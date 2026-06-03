"""SQLAlchemy models for Cyber-Team."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
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
