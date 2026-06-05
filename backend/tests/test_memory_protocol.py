import pytest

from cyber_team.memory.protocol import AgentMemoryProtocol


class FakeMemory:
    def __init__(self):
        self.recall_requests = []
        self.entries = []
        self.traces = []

    async def recall(self, data):
        self.recall_requests.append(data)
        return [
            {
                "id": "legacy-memory-1",
                "content": "Legacy namespace memory.",
                "memory_type": "episodic",
                "namespace": data.namespace,
                "agent_id": data.agent_id,
                "importance": 0.7,
                "score": 1.0,
            }
        ]

    async def remember(self, data):
        entry = {
            "id": "written-memory-1",
            "agent_id": data.agent_id,
            "memory_type": data.memory_type,
            "namespace": data.namespace,
            "content": data.content,
            "metadata": data.metadata,
            "importance": data.importance,
        }
        self.entries.append(entry)
        return entry

    async def record_trace(self, data):
        trace = {
            "invocation_id": data.invocation_id,
            "agent_id": data.agent_id,
            "source_type": data.source_type,
            "task_excerpt": data.task_excerpt,
            "memory_namespace": data.memory_namespace,
            "read_policy": data.read_policy,
            "write_policy": data.write_policy,
            "recalled_memory_ids": data.recalled_memory_ids,
            "written_memory_ids": data.written_memory_ids,
            "recall_count": data.recall_count,
            "write_count": data.write_count,
            "errors": data.errors,
            "metadata": data.metadata,
        }
        self.traces.append(trace)
        return trace


class PolicyMemory(FakeMemory):
    async def recall_with_policy(self, data):
        return {
            "items": [
                {
                    "id": "private-memory",
                    "content": "Private memory.",
                    "memory_type": "episodic",
                    "namespace": data.memory_namespace,
                    "agent_id": data.agent_id,
                    "importance": 0.7,
                    "score": 1.0,
                    "scope": "agent_private",
                },
                {
                    "id": "company-memory",
                    "content": "Company memory.",
                    "memory_type": "semantic",
                    "namespace": "company:acme",
                    "agent_id": None,
                    "importance": 0.9,
                    "score": 1.0,
                    "scope": "company_constitution",
                },
            ],
            "policy": {
                "version": "memory-policy-v1",
                "strategy": "agent-private-plus-company-shared",
                "agent_id": data.agent_id,
                "role_family": data.role_family,
                "role_name": data.role_name,
                "memory_namespace": data.memory_namespace,
                "company_namespace": "company:acme",
                "limit": data.limit,
                "scopes": [
                    {"name": "agent_private", "namespace": data.memory_namespace},
                    {"name": "company_constitution", "namespace": "company:acme"},
                ],
                "scope_results": [
                    {"name": "agent_private", "returned": 1, "added": 1},
                    {"name": "company_constitution", "returned": 1, "added": 1},
                ],
            },
            "errors": [],
        }


def agent():
    return {
        "id": "ops_agent",
        "role_family": "operations",
        "role_name": "Operations Manager",
        "memory_namespace": "company:acme:ops",
    }


@pytest.mark.asyncio
async def test_memory_protocol_prepares_policy_prompt_context_and_write_trace():
    memory = PolicyMemory()
    protocol = AgentMemoryProtocol(memory)

    context = await protocol.prepare_invocation(
        agent=agent(),
        task="Prepare the launch brief.",
        invocation_id="invoke-1",
        conversation_id="conversation-1",
        source_type="chat",
    )
    await protocol.complete_invocation(context, result="Launch brief complete.")

    assert context.company_namespace == "company:acme"
    assert context.recalled_memory_ids == ["private-memory", "company-memory"]
    assert "Memory protocol context" in context.prompt_context
    assert "[company_constitution] Company memory." in context.prompt_context
    assert memory.entries[0]["memory_type"] == "episodic"
    assert memory.entries[0]["metadata"]["protocol_version"] == "agent-memory-protocol-v1"
    assert memory.entries[0]["metadata"]["recalled_memory_ids"] == [
        "private-memory",
        "company-memory",
    ]
    assert memory.traces[0]["read_policy"]["strategy"] == (
        "agent-private-plus-company-shared"
    )
    assert memory.traces[0]["source_type"] == "chat"
    assert memory.traces[0]["metadata"]["coverage"] == "hit"
    assert memory.traces[0]["write_policy"]["version"] == "memory-write-policy-v1"
    assert memory.traces[0]["metadata"]["memory_coverage"] == "hit"
    assert memory.traces[0]["metadata"]["protocol_version"] == "agent-memory-protocol-v1"


@pytest.mark.asyncio
async def test_memory_protocol_falls_back_to_legacy_namespace_recall():
    memory = FakeMemory()
    protocol = AgentMemoryProtocol(memory)

    context = await protocol.prepare_invocation(
        agent=agent(),
        task="Prepare the launch brief.",
        invocation_id="invoke-legacy",
    )

    assert memory.recall_requests[0].namespace == "company:acme:ops"
    assert context.read_policy["version"] == "legacy-agent-namespace"
    assert context.read_policy["strategy"] == "agent-namespace-only"
    assert context.recalled_memory_ids == ["legacy-memory-1"]
    assert "[agent_private] Legacy namespace memory." in context.prompt_context


@pytest.mark.asyncio
async def test_memory_protocol_records_memory_service_unavailable_without_crashing():
    protocol = AgentMemoryProtocol(None)

    context = await protocol.prepare_invocation(
        agent=agent(),
        task="Prepare the launch brief.",
        invocation_id="invoke-no-memory",
    )
    await protocol.complete_invocation(context, result="Launch brief complete.")

    assert context.errors == ["memory_service:unavailable"]
    assert context.prompt_context == ""
    assert context.written_memory_ids == []


@pytest.mark.asyncio
async def test_memory_protocol_records_failure_trace_without_memory_write():
    memory = PolicyMemory()
    protocol = AgentMemoryProtocol(memory)
    context = await protocol.prepare_invocation(
        agent=agent(),
        task="Prepare the launch brief.",
        invocation_id="invoke-failed",
    )

    await protocol.record_failure(
        context,
        exc=RuntimeError("provider unavailable"),
        trace_metadata={"test": True},
    )

    assert memory.entries == []
    assert memory.traces[0]["written_memory_ids"] == []
    assert memory.traces[0]["write_count"] == 0
    assert memory.traces[0]["metadata"]["test"] is True
    assert memory.traces[0]["errors"] == [
        "invoke:RuntimeError:provider unavailable",
    ]
