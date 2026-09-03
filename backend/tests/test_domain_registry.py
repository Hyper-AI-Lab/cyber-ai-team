import pytest
from pydantic import ValidationError

from cyber_team.company.domain_registry import (
    DomainRegistry,
    DomainSpecification,
    canonical_domain_key,
)


def test_builtin_registry_preserves_existing_domain_catalog_and_routes():
    registry = DomainRegistry.builtin()

    assert len(registry.keys()) == 15
    assert registry.get("knowledge").core is True
    assert registry.route_event("erpnext.sales_invoice.created") == "finance"
    assert registry.route_event("evidence.email.received") == "support"
    assert registry.route_event("unknown", '{"credential": "changed"}') == "security"
    assert registry.route_event("unknown", "ordinary process work") == "operations"


def test_registry_accepts_data_only_custom_domain_and_routes_by_specificity():
    registry = DomainRegistry(
        [
            {
                "key": "partner_success",
                "display_name": "Partner Success",
                "purpose": "Coordinate partner outcomes.",
                "inputs": ["partner.signal"],
                "outputs": ["partner_assessment"],
                "event_selectors": ["partner"],
                "required_capabilities": ["partner_management"],
                "default_role_name": "Partner Success Agent",
            },
            {
                "key": "partner_contracts",
                "display_name": "Partner Contracts",
                "purpose": "Review partner contract evidence.",
                "inputs": ["partner.contract"],
                "outputs": ["contract_assessment"],
                "event_selectors": ["partner.contract"],
                "required_capabilities": ["contract_review"],
                "default_role_name": "Partner Contract Agent",
                "risk_level": "high",
            },
        ]
    )

    assert registry.route_event("partner.contract.received") == "partner_contracts"
    assert registry.require("partner-success").key == "partner_success"


def test_registry_rejects_code_fields_duplicates_and_unbounded_catalogs():
    with pytest.raises(ValidationError):
        DomainSpecification.model_validate(
            {
                "key": "unsafe",
                "display_name": "Unsafe",
                "purpose": "Attempt to load code.",
                "default_role_name": "Unsafe Agent",
                "executor": "import os",
            }
        )

    item = DomainSpecification(
        key="custom_domain",
        display_name="Custom Domain",
        purpose="Perform custom advisory work.",
        default_role_name="Custom Agent",
    )
    with pytest.raises(ValueError, match="Duplicate"):
        DomainRegistry([item, item])
    second_item = item.model_copy(
        update={"key": "another_domain", "display_name": "Another Domain"}
    )
    with pytest.raises(ValueError, match="cannot exceed"):
        DomainRegistry([item, second_item], max_domains=1)


def test_domain_aliases_are_canonical_and_stable():
    assert canonical_domain_key("Project Management") == "product"
    assert canonical_domain_key("research") == "knowledge"
    assert canonical_domain_key("partner-success") == "partner_success"
