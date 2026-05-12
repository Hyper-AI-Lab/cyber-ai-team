package cyberteam.authz

import rego.v1

# Default deny
default allow := false

# Owner can do everything
allow if {
    input.role == "owner"
}

allow if {
    input.role == "agent"
    input.resource_type == "tool"
    input.action == "read"
    input.resource_id == null
}

allow if {
    input.role == "agent"
    input.resource_type == "tool"
    input.action in {"read", "execute"}
    input.resource_id in input.allowed_tools
}

allow if {
    input.role == "agent"
    input.resource_type == "memory_namespace"
    input.action in {"read", "write"}
    input.resource_id == input.memory_namespace
}

# Agents can invoke their own tools
allow if {
    input.action == "invoke_tool"
    input.agent_id == input.requesting_agent_id
}

# Sensitive actions require approval
require_approval contains input.action if {
    input.action == "send_payment"
}

require_approval contains input.action if {
    input.action == "sign_contract"
}

require_approval contains input.action if {
    input.action == "delete_data"
}

require_approval contains input.action if {
    input.action == "modify_production"
}

require_approval contains input.action if {
    input.action == "send_external_communication"
    input.channel == "sms"
}

require_approval contains input.action if {
    input.action == "send_external_communication"
    input.channel == "voice"
}

# Auto-approve for low-risk actions
auto_approve contains input.action if {
    input.action == "read_memory"
}

auto_approve contains input.action if {
    input.action == "write_memory"
    input.memory_type == "episodic"
}

auto_approve contains input.action if {
    input.action == "invoke_tool"
    input.tool_name == "web_search"
}
