
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


def test_role_gap_summary_endpoint_success(test_app_client, mock_agent_manager):
    mock_agent_manager.summarize_role_backlog.return_value = {
        "items": [{"gap_id": "gap_123", "business_function": "Communications"}],
        "groups": [{"business_function": "Communications", "count": 1}],
        "counts": {"total": 1},
        "blocking_count": 0,
        "approval_count": 1,
        "expired_approval_count": 0,
    }

    response = test_app_client.get(
        "/api/roles/role-gaps/summary"
        "?status=open,proposed&source_type=company_context_snapshot&limit=25"
    )

    assert response.status_code == 200
    assert response.json()["counts"]["total"] == 1
    mock_agent_manager.summarize_role_backlog.assert_called_once_with(
        statuses=["open", "proposed"],
        source_type="company_context_snapshot",
        limit=25,
    )


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


def test_role_gap_regenerate_approval_endpoint_success(test_app_client, mock_agent_manager):
    mock_agent_manager.regenerate_role_gap_approval.return_value = {
        "approval_id": "approval_123",
        "item": {"gap_id": "gap_123", "approval": {"state": "pending"}},
    }

    response = test_app_client.post(
        "/api/roles/role-gaps/gap_123/approval/regenerate",
        json={"company_profile": {"name": "Acme"}},
    )

    assert response.status_code == 200
    assert response.json()["approval_id"] == "approval_123"
    mock_agent_manager.regenerate_role_gap_approval.assert_called_once_with(
        "gap_123",
        {"name": "Acme"},
        requested_by="owner@example.com",
    )


def test_role_gap_apply_endpoint_passes_approval_id(test_app_client, mock_agent_manager):
    mock_agent_manager.apply_role_gap_proposal.return_value = {
        "id": "gap_123",
        "status": "resolved",
        "resolution": {
            "agent_id": "outbound_calling_specialist",
            "approval_id": "approval_123",
        },
    }

    response = test_app_client.post(
        "/api/roles/role-gaps/gap_123/apply",
        json={
            "company_profile": {"name": "Acme"},
            "approval_id": "approval_123",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "resolved"
    mock_agent_manager.apply_role_gap_proposal.assert_called_once_with(
        "gap_123",
        {"name": "Acme"},
        approval_id="approval_123",
        requested_by="owner@example.com",
    )


def test_supervisor_role_gap_review_endpoint(test_app_client):
    test_app_client.app.state.supervisor_review_service.run_once.return_value = {
        "reviewed_at": "2026-06-01T00:00:00",
        "actor": "owner@example.com",
        "role_gaps_reviewed": 1,
        "role_gaps_proposed": ["gap_123"],
        "role_gap_recommendations": [],
        "stale_approvals": [],
        "workflow_failure_gaps": [],
    }

    response = test_app_client.post("/api/roles/role-gaps/supervisor-review")

    assert response.status_code == 200
    assert response.json()["role_gaps_proposed"] == ["gap_123"]
    test_app_client.app.state.supervisor_review_service.run_once.assert_called_once_with(
        actor="owner@example.com"
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
