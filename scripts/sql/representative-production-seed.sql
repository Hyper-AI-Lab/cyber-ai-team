INSERT INTO agents (
    id,
    role_family,
    role_name,
    instructions,
    tools,
    memory_namespace,
    approval_policy,
    status,
    config,
    created_at,
    updated_at
)
SELECT
    'agent-' || g,
    'operations',
    'Synthetic Agent ' || g,
    'Operate representative migration rehearsal workload.',
    '[]'::json,
    'tenant:synth:agent-' || g,
    'manual',
    'active',
    json_build_object('seed', true, 'ordinal', g),
    now() - (g || ' days')::interval,
    now() - (g || ' hours')::interval
FROM generate_series(1, :row_count) AS g
ON CONFLICT (id) DO NOTHING;

INSERT INTO role_manifests (
    id,
    family,
    name,
    description,
    instructions_template,
    default_tools,
    memory_namespace,
    approval_policy,
    success_metrics,
    is_core,
    config
)
SELECT
    'role-' || g,
    'operations',
    'Synthetic Role ' || g,
    'Representative role manifest for migration rehearsal.',
    'Template ' || g,
    '[]'::json,
    'tenant:synth:role-' || g,
    'manual',
    '{}'::json,
    false,
    '{}'::json
FROM generate_series(1, :row_count) AS g
ON CONFLICT (id) DO NOTHING;

INSERT INTO workflows (
    id,
    name,
    description,
    graph_definition,
    status,
    trigger_type,
    trigger_config,
    created_at,
    updated_at
)
SELECT
    'workflow-' || g,
    'Synthetic Workflow ' || g,
    'Representative workflow for migration rehearsal.',
    json_build_object('nodes', json_build_array('start', 'approval', 'finish')),
    CASE WHEN g % 3 = 0 THEN 'active' ELSE 'draft' END,
    'manual',
    '{}'::json,
    now() - (g || ' days')::interval,
    now() - (g || ' hours')::interval
FROM generate_series(1, :row_count) AS g
ON CONFLICT (id) DO NOTHING;

INSERT INTO workflow_runs (
    id,
    workflow_id,
    status,
    current_node,
    state,
    result,
    error,
    started_at,
    completed_at
)
SELECT
    'run-' || g,
    'workflow-' || (((g - 1) % :row_count) + 1),
    CASE WHEN g % 4 = 0 THEN 'failed' ELSE 'completed' END,
    'finish',
    json_build_object('subject_id', 'customer-' || (((g - 1) % 10) + 1)),
    json_build_object('ok', g % 4 <> 0),
    CASE WHEN g % 4 = 0 THEN 'synthetic failure' ELSE NULL END,
    now() - (g || ' days')::interval,
    now() - (g || ' days')::interval + interval '5 minutes'
FROM generate_series(1, :row_count) AS g
ON CONFLICT (id) DO NOTHING;

INSERT INTO memory_entries (
    id,
    agent_id,
    memory_type,
    namespace,
    content,
    metadata,
    importance,
    created_at,
    expires_at
)
SELECT
    'memory-' || g,
    'agent-' || (((g - 1) % :row_count) + 1),
    CASE WHEN g % 5 = 0 THEN 'pinned' ELSE 'episodic' END,
    'entity:customer-' || (((g - 1) % 10) + 1),
    'Representative memory ' || g,
    json_build_object('subject_id', 'customer-' || (((g - 1) % 10) + 1)),
    0.1 + ((g % 9)::float / 10.0),
    now() - (g || ' days')::interval,
    CASE WHEN g % 7 = 0 THEN now() - interval '1 day' ELSE NULL END
FROM generate_series(1, :row_count) AS g
ON CONFLICT (id) DO NOTHING;

INSERT INTO approval_requests (
    id,
    agent_id,
    action_type,
    action_description,
    action_payload,
    requester,
    requester_type,
    risk_level,
    target_type,
    target_id,
    status,
    reviewer,
    review_note,
    consumed_at,
    expires_at,
    created_at,
    resolved_at
)
SELECT
    'approval-' || g,
    CASE WHEN g % 6 = 0 THEN NULL ELSE 'agent-' || (((g - 1) % :row_count) + 1) END,
    'send_email',
    'Representative approval ' || g,
    json_build_object('subject_id', 'customer-' || (((g - 1) % 10) + 1)),
    'system',
    'system',
    CASE WHEN g % 3 = 0 THEN 'high' ELSE 'medium' END,
    'customer',
    'customer-' || (((g - 1) % 10) + 1),
    CASE WHEN g % 5 = 0 THEN 'pending' ELSE 'approved' END,
    CASE WHEN g % 5 = 0 THEN NULL ELSE 'owner' END,
    NULL,
    NULL,
    CASE WHEN g % 5 = 0 THEN now() + interval '1 day' ELSE NULL END,
    now() - (g || ' days')::interval,
    CASE WHEN g % 5 = 0 THEN NULL ELSE now() - (g || ' days')::interval END
FROM generate_series(1, :row_count) AS g
ON CONFLICT (id) DO NOTHING;

INSERT INTO audit_events (
    id,
    event_type,
    actor,
    actor_type,
    resource_type,
    resource_id,
    action,
    outcome,
    metadata,
    created_at
)
SELECT
    'audit-' || g,
    'synthetic.event',
    CASE WHEN g % 2 = 0 THEN 'owner@example.com' ELSE 'agent-' || (((g - 1) % :row_count) + 1) END,
    CASE WHEN g % 2 = 0 THEN 'owner' ELSE 'agent' END,
    'customer',
    'customer-' || (((g - 1) % 10) + 1),
    'exercise',
    CASE WHEN g % 8 = 0 THEN 'failure' ELSE 'success' END,
    json_build_object('seed', true, 'ordinal', g),
    now() - (g || ' days')::interval
FROM generate_series(1, :row_count) AS g
ON CONFLICT (id) DO NOTHING;

INSERT INTO communication_logs (
    id,
    agent_id,
    channel,
    direction,
    recipient,
    content,
    metadata,
    status,
    created_at
)
SELECT
    'comm-' || g,
    'agent-' || (((g - 1) % :row_count) + 1),
    CASE WHEN g % 3 = 0 THEN 'sms' WHEN g % 3 = 1 THEN 'email' ELSE 'voice' END,
    'outbound',
    'customer-' || (((g - 1) % 10) + 1),
    'Representative communication ' || g,
    json_build_object('subject_id', 'customer-' || (((g - 1) % 10) + 1)),
    CASE WHEN g % 9 = 0 THEN 'failed' ELSE 'sent' END,
    now() - (g || ' days')::interval
FROM generate_series(1, :row_count) AS g
ON CONFLICT (id) DO NOTHING;
