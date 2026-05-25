"""Load default role manifests from YAML into the database on first startup."""

import logging
from pathlib import Path

import yaml
from sqlalchemy import select

from cyber_team.agents.manager import AgentManager
from cyber_team.db import async_session
from cyber_team.db.models import RoleManifest

logger = logging.getLogger(__name__)

DEFAULTS_PATH = Path(__file__).parent / "defaults.yaml"


async def load_default_roles():
    """Load default role manifests from YAML if not already in DB."""
    async with async_session() as session:
        existing = await session.execute(select(RoleManifest))
        if existing.scalars().first():
            logger.info("Role manifests already exist, skipping defaults load")
            return

    if not DEFAULTS_PATH.exists():
        logger.warning(f"Defaults file not found: {DEFAULTS_PATH}")
        return

    with open(DEFAULTS_PATH) as f:
        data = yaml.safe_load(f)

    mgr = AgentManager()  # lightweight — only needs DB access for manifest creation
    count = 0
    for role in data.get("roles", []):
        try:
            create_data = type("RoleManifestCreate", (), {
                "family": role["family"],
                "name": role["name"],
                "description": role["description"],
                "instructions_template": role["instructions_template"],
                "default_tools": role.get("default_tools", []),
                "memory_namespace": f"{role['family']}:{role['family']}",
                "approval_policy": role.get("approval_policy", "auto"),
                "success_metrics": role.get("success_metrics", []),
                "is_core": True,
                "config": {},
            })()
            await mgr.create_role_manifest(create_data)
            logger.info(f"Loaded role manifest: {role['name']}")
            count += 1
        except Exception as e:
            logger.error(f"Failed to load role {role.get('name', '?')}: {e}")

    logger.info(f"Loaded {count} default role manifests")
