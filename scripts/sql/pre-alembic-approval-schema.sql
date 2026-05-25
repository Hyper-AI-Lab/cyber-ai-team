CREATE TABLE agents (
    id VARCHAR(64) PRIMARY KEY,
    role_family VARCHAR(100) NOT NULL,
    role_name VARCHAR(200) NOT NULL,
    instructions TEXT NOT NULL,
    tools JSON NOT NULL,
    memory_namespace VARCHAR(200) NOT NULL,
    approval_policy VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    config JSON NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);

CREATE TABLE approval_requests (
    id VARCHAR(64) PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL REFERENCES agents(id),
    action_type VARCHAR(100) NOT NULL,
    action_description TEXT NOT NULL,
    action_payload JSON NOT NULL,
    status VARCHAR(20) NOT NULL,
    reviewer VARCHAR(200),
    review_note TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    resolved_at TIMESTAMP WITHOUT TIME ZONE
);

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
) VALUES (
    'legacy-agent',
    'sales',
    'Legacy Sales',
    'Legacy instructions',
    '[]',
    'sales:legacy',
    'auto',
    'active',
    '{}',
    '2026-01-01 00:00:00',
    '2026-01-01 00:00:00'
);

INSERT INTO approval_requests (
    id,
    agent_id,
    action_type,
    action_description,
    action_payload,
    status,
    created_at
) VALUES (
    'legacy-approval-1',
    'legacy-agent',
    'send_email',
    'Legacy approval request',
    '{"recipient": "customer@example.com"}',
    'pending',
    '2026-01-01 00:00:00'
);
