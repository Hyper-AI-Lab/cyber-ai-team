"""Memory protocol helpers for agent execution.

This module keeps memory retrieval, durable write, and trace recording rules
explicit instead of scattering them through agent orchestration code.
"""

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

PROTOCOL_VERSION = "agent-memory-protocol-v1"
WRITE_POLICY_VERSION = "memory-write-policy-v1"


@dataclass
class AgentMemoryContext:
    invocation_id: str
    agent_id: str
    conversation_id: str | None
    source_type: str
    task: str
    task_excerpt: str
    memory_namespace: str
    company_namespace: str | None
    role_family: str
    role_name: str
    prompt_context: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    recalled_memory_ids: list[str] = field(default_factory=list)
    written_memory_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    read_policy: dict[str, Any] = field(default_factory=dict)
    write_policy: dict[str, Any] = field(default_factory=dict)

    @property
    def coverage(self) -> str:
        return "hit" if self.items else "empty"


class AgentMemoryProtocol:
    def __init__(self, memory_service: Any | None, metrics_service: Any | None = None):
        self._memory = memory_service
        self._metrics = metrics_service

    async def prepare_invocation(
        self,
        *,
        agent: dict[str, Any],
        task: str,
        invocation_id: str,
        conversation_id: str | None = None,
        source_type: str = "agent_invocation",
        limit: int = 8,
    ) -> AgentMemoryContext:
        agent_id = str(agent["id"])
        memory_namespace = str(agent["memory_namespace"])
        role_family = str(agent["role_family"])
        role_name = str(agent["role_name"])
        company_namespace = self.company_namespace_from_memory_namespace(memory_namespace)
        context = AgentMemoryContext(
            invocation_id=invocation_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            source_type=source_type,
            task=task,
            task_excerpt=self.excerpt(task, 500),
            memory_namespace=memory_namespace,
            company_namespace=company_namespace,
            role_family=role_family,
            role_name=role_name,
            read_policy=self.default_read_policy(
                agent_id=agent_id,
                memory_namespace=memory_namespace,
                role_family=role_family,
                role_name=role_name,
                limit=limit,
            ),
            write_policy=self.default_write_policy(
                memory_namespace=memory_namespace,
                company_namespace=company_namespace,
            ),
        )

        if not self._memory:
            context.errors.append("memory_service:unavailable")
            self._record_memory_metric("recall", "unavailable", source_type)
            return context

        try:
            recall_result = await self._recall(
                query=task,
                agent_id=agent_id,
                memory_namespace=memory_namespace,
                role_family=role_family,
                role_name=role_name,
                limit=limit,
            )
            context.items = list(recall_result.get("items", []))
            context.read_policy = recall_result.get("policy") or context.read_policy
            context.errors.extend(recall_result.get("errors", []))
            context.recalled_memory_ids = [
                str(memory["id"]) for memory in context.items if memory.get("id")
            ]
            context.prompt_context = self.prompt_context(context)
            self._record_memory_metric("recall", "success", source_type)
        except Exception as exc:
            context.errors.append(
                f"recall:{type(exc).__name__}:{self.excerpt(str(exc), 160)}"
            )
            self._record_memory_metric("recall", "failed", source_type)

        return context

    async def complete_invocation(
        self,
        context: AgentMemoryContext,
        *,
        result: str,
        trace_metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._memory:
            return
        try:
            memory_entry = await self._memory.remember(
                SimpleNamespace(
                    agent_id=context.agent_id,
                    memory_type=context.write_policy["memory_type"],
                    namespace=context.memory_namespace,
                    content=self.invocation_summary(context.task, result),
                    metadata=self.write_metadata(context, result),
                    importance=context.write_policy["importance"],
                )
            )
            if memory_entry.get("id"):
                context.written_memory_ids.append(str(memory_entry["id"]))
            self._record_memory_metric("write", "success", context.source_type)
        except Exception as exc:
            context.errors.append(
                f"write:{type(exc).__name__}:{self.excerpt(str(exc), 160)}"
            )
            self._record_memory_metric("write", "failed", context.source_type)
        await self.record_trace(context, result=result, metadata=trace_metadata)

    async def record_failure(
        self,
        context: AgentMemoryContext,
        *,
        exc: Exception,
        trace_metadata: dict[str, Any] | None = None,
    ) -> None:
        context.errors.append(
            f"invoke:{type(exc).__name__}:{self.excerpt(str(exc), 160)}"
        )
        await self.record_trace(context, result="", metadata=trace_metadata)

    async def record_trace(
        self,
        context: AgentMemoryContext,
        *,
        result: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._memory:
            return
        record_trace = getattr(self._memory, "record_trace", None)
        if not record_trace:
            return
        try:
            await record_trace(
                SimpleNamespace(
                    invocation_id=context.invocation_id,
                    agent_id=context.agent_id,
                    conversation_id=context.conversation_id,
                    source_type=context.source_type,
                    task_excerpt=context.task_excerpt,
                    memory_namespace=context.memory_namespace,
                    read_policy=context.read_policy,
                    write_policy=context.write_policy,
                    recalled_memory_ids=context.recalled_memory_ids,
                    written_memory_ids=context.written_memory_ids,
                    recall_count=len(context.recalled_memory_ids),
                    write_count=len(context.written_memory_ids),
                    errors=context.errors,
                    metadata={
                        "protocol_version": PROTOCOL_VERSION,
                        "role_family": context.role_family,
                        "role_name": context.role_name,
                        "company_namespace": context.company_namespace,
                        "result_excerpt": self.excerpt(result, 500),
                        "memory_scope_results": context.read_policy.get(
                            "scope_results",
                            [],
                        ),
                        "coverage": context.coverage,
                        "memory_coverage": context.coverage,
                        **(metadata or {}),
                    },
                )
            )
        except Exception:
            # Trace writes must never break agent execution.
            return

    async def _recall(
        self,
        *,
        query: str,
        agent_id: str,
        memory_namespace: str,
        role_family: str,
        role_name: str,
        limit: int,
    ) -> dict[str, Any]:
        recall_with_policy = getattr(self._memory, "recall_with_policy", None)
        if recall_with_policy:
            return await recall_with_policy(
                SimpleNamespace(
                    query=query,
                    agent_id=agent_id,
                    memory_namespace=memory_namespace,
                    role_family=role_family,
                    role_name=role_name,
                    limit=limit,
                )
            )

        items = await self._memory.recall(
            SimpleNamespace(
                query=query,
                agent_id=agent_id,
                namespace=memory_namespace,
                memory_type=None,
                limit=min(limit, 5),
            )
        )
        return {
            "items": items,
            "policy": self.default_read_policy(
                agent_id=agent_id,
                memory_namespace=memory_namespace,
                role_family=role_family,
                role_name=role_name,
                limit=min(limit, 5),
                version="legacy-agent-namespace",
                strategy="agent-namespace-only",
            ),
            "errors": [],
        }

    def prompt_context(self, context: AgentMemoryContext) -> str:
        if not context.items:
            return ""
        lines = [
            "",
            "",
            "Memory protocol context:",
            f"- Protocol: {PROTOCOL_VERSION}",
            f"- Read strategy: {context.read_policy.get('strategy', 'unknown')}",
            "- Treat recalled memory as operational context; canonical records "
            "override memory on conflict.",
            "- Preserve provenance when you rely on remembered facts.",
            "Relevant memories:",
        ]
        for memory in context.items[:8]:
            scope = memory.get("scope") or "agent_private"
            content = str(memory.get("content") or "").strip()
            if content:
                lines.append(f"- [{scope}] {content}")
        return "\n".join(lines)

    def write_metadata(
        self,
        context: AgentMemoryContext,
        result: str,
    ) -> dict[str, Any]:
        return {
            "type": "agent_invocation_summary",
            "protocol_version": PROTOCOL_VERSION,
            "invocation_id": context.invocation_id,
            "source_type": context.source_type,
            "task_excerpt": context.task_excerpt,
            "result_excerpt": self.excerpt(result, 500),
            "read_policy_version": context.read_policy.get("version"),
            "write_policy_version": context.write_policy.get("version"),
            "recalled_memory_ids": context.recalled_memory_ids,
            "memory_coverage": context.coverage,
            "company_namespace": context.company_namespace,
            "traceable": True,
        }

    def invocation_summary(self, task: str, result: str) -> str:
        return (
            f"Task: {self.excerpt(task, 240)} | "
            f"Result: {self.excerpt(result, 360)}"
        )

    def _record_memory_metric(self, operation: str, status: str, source_type: str) -> None:
        if self._metrics:
            self._metrics.record_memory_operation(operation, status, source_type)

    @staticmethod
    def default_read_policy(
        *,
        agent_id: str,
        memory_namespace: str,
        role_family: str,
        role_name: str,
        limit: int,
        version: str = "memory-policy-v1",
        strategy: str = "agent-private-plus-company-shared",
    ) -> dict[str, Any]:
        return {
            "version": version,
            "strategy": strategy,
            "agent_id": agent_id,
            "role_family": role_family,
            "role_name": role_name,
            "memory_namespace": memory_namespace,
            "limit": limit,
            "scopes": [
                {
                    "name": "agent_private",
                    "namespace": memory_namespace,
                    "agent_id": agent_id,
                    "memory_type": None,
                    "limit": min(limit, 4),
                }
            ],
        }

    @staticmethod
    def default_write_policy(
        *,
        memory_namespace: str,
        company_namespace: str | None,
    ) -> dict[str, Any]:
        return {
            "version": WRITE_POLICY_VERSION,
            "strategy": "durable-episodic-invocation-summary",
            "memory_type": "episodic",
            "namespace": memory_namespace,
            "company_namespace": company_namespace,
            "importance": 0.6,
            "summary_limit_chars": 600,
            "rules": [
                "Write concise task/result summaries after completed work.",
                "Attach invocation provenance and recalled memory ids.",
                "Do not treat memory as canonical when system-of-record data conflicts.",
            ],
        }

    @staticmethod
    def company_namespace_from_memory_namespace(
        memory_namespace: str | None,
    ) -> str | None:
        if not memory_namespace:
            return None
        parts = memory_namespace.split(":")
        if len(parts) >= 2 and parts[0] == "company":
            return ":".join(parts[:2])
        return None

    @staticmethod
    def excerpt(text: str, limit: int = 240) -> str:
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."
