
def test_provision_role_endpoint_success(test_app_client, mock_agent_manager):
    # Prepare mock return data for creating the manifest
    mock_agent_manager.create_role_manifest.return_value = {
        "id": "tax_advisor",
        "family": "finance",
        "name": "Tax Advisor",
        "description": "Consults on startup tax matters",
        "instructions_template": "Draft guidelines for tax compliance.",
        "default_tools": ["contract_draft"],
        "memory_namespace": "finance:tax_advisor",
        "approval_policy": "auto",
        "success_metrics": [],
        "is_core": True,
        "config": {}
    }

    payload = {
        "family": "finance",
        "name": "Tax Advisor",
        "description": "Consults on startup tax matters",
        "instructions_template": "Draft guidelines for tax compliance.",
        "default_tools": ["contract_draft"],
        "memory_namespace": "finance:tax_advisor",
        "approval_policy": "auto",
        "success_metrics": [],
        "is_core": True,
        "config": {}
    }

    response = test_app_client.post("/api/roles/provision", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["id"] == "tax_advisor"
    assert data["family"] == "finance"
    assert data["name"] == "Tax Advisor"

    # Confirm role creation and agent instantiation were both invoked
    mock_agent_manager.get_role_manifest.assert_called_once_with("tax_advisor")
    mock_agent_manager.create_role_manifest.assert_called_once()
    mock_agent_manager.instantiate_role.assert_called_once_with("tax_advisor")

def test_provision_role_endpoint_duplicate_rejected(test_app_client, mock_agent_manager):
    # Set mock to simulate existing manifest
    mock_agent_manager.get_role_manifest.return_value = {"id": "tax_advisor"}

    payload = {
        "family": "finance",
        "name": "Tax Advisor",
        "description": "Consults on startup tax matters",
        "instructions_template": "Draft guidelines for tax compliance.",
        "default_tools": ["contract_draft"],
        "memory_namespace": "finance:tax_advisor",
        "approval_policy": "auto",
        "success_metrics": [],
        "is_core": True,
        "config": {}
    }

    response = test_app_client.post("/api/roles/provision", json=payload)

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]

    # Confirm it did NOT call create or instantiate
    mock_agent_manager.create_role_manifest.assert_not_called()
    mock_agent_manager.instantiate_role.assert_not_called()


def test_report_role_gap_endpoint_success(test_app_client, mock_agent_manager):
    mock_agent_manager.report_role_gap.return_value = {
        "id": "gap_123",
        "title": "Need phone outreach",
        "description": "Sales is blocked because outbound calls are needed.",
        "status": "open",
        "severity": "high",
        "source_agent_id": "sales",
        "source_type": "agent",
        "company_namespace": "company:acme",
        "capability": "outbound_voice",
        "requested_tools": ["make_call"],
        "context": {"lead_count": 3},
        "proposed_role": {},
        "resolution": {},
        "created_at": "2026-06-01T00:00:00",
        "updated_at": "2026-06-01T00:00:00",
        "resolved_at": None,
    }

    response = test_app_client.post(
        "/api/roles/role-gaps",
        json={
            "title": "Need phone outreach",
            "description": "Sales is blocked because outbound calls are needed.",
            "severity": "high",
            "source_agent_id": "sales",
            "source_type": "agent",
            "company_namespace": "company:acme",
            "capability": "outbound_voice",
            "requested_tools": ["make_call"],
            "context": {"lead_count": 3},
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["id"] == "gap_123"
    assert data["status"] == "open"
    mock_agent_manager.report_role_gap.assert_called_once()


def test_role_gap_proposal_endpoint_success(test_app_client, mock_agent_manager):
    mock_agent_manager.propose_role_for_gap.return_value = {
        "id": "gap_123",
        "status": "proposed",
        "proposed_role": {
            "manifest_payload": {
                "family": "communications",
                "name": "Outbound Calling Specialist",
                "default_tools": ["make_call"],
            }
        },
    }

    response = test_app_client.post(
        "/api/roles/role-gaps/gap_123/proposal",
        json={"company_profile": {"name": "Acme"}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "proposed"
    mock_agent_manager.propose_role_for_gap.assert_called_once_with(
        "gap_123",
        {"name": "Acme"},
    )


def test_legacy_role_gap_endpoint_persists_and_returns_proposal(
    test_app_client,
    mock_agent_manager,
):
    mock_agent_manager.report_role_gap.return_value = {"id": "gap_legacy"}
    mock_agent_manager.propose_role_for_gap.return_value = {
        "id": "gap_legacy",
        "status": "proposed",
        "proposed_role": {"manifest_payload": {"name": "Gap Specialist"}},
    }

    response = test_app_client.post(
        "/api/roles/role-gap",
        json={"gap_description": "Need a specialist for partner onboarding."},
    )

    assert response.status_code == 200
    assert response.json()["gap_id"] == "gap_legacy"
    assert response.json()["proposal"]["manifest_payload"]["name"] == "Gap Specialist"
    mock_agent_manager.report_role_gap.assert_called_once()
    mock_agent_manager.propose_role_for_gap.assert_called_once_with("gap_legacy")
