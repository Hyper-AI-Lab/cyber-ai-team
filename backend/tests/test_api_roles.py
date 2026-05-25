
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
