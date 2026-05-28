import pytest

from cyber_team.config import Settings


def production_settings(**overrides):
    values = {
        "environment": "production",
        "secret_key": "prod-secret",
        "owner_password": "custom-owner-password",
        "owner_password_hash": "$2b$12$abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNO",
        "postgres_password": "prod-postgres-password",
        "redis_password": "prod-redis-password",
        "cors_allowed_origins": "https://console.example.com",
        "communications_allow_simulation": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_runtime_config_requires_owner_password_hash():
    settings = production_settings(owner_password_hash="")

    with pytest.raises(RuntimeError, match="OWNER_PASSWORD_HASH"):
        settings.validate_runtime_config()


def test_production_runtime_config_rejects_wildcard_cors():
    settings = production_settings(cors_allowed_origins="*")

    with pytest.raises(RuntimeError, match="wildcard CORS"):
        settings.validate_runtime_config()


def test_production_runtime_config_rejects_simulated_communications():
    settings = production_settings(communications_allow_simulation=True)

    with pytest.raises(RuntimeError, match="COMMUNICATIONS_ALLOW_SIMULATION"):
        settings.validate_runtime_config()


def test_production_runtime_config_accepts_hardened_values():
    settings = production_settings()

    settings.validate_runtime_config()


def test_connection_urls_escape_reserved_characters():
    settings = Settings(
        postgres_user="cyber/team",
        postgres_password="pg/pass@word:with#chars",
        postgres_db="cyber/team",
        redis_password="redis/pass@word:with#chars",
    )

    assert (
        settings.postgres_dsn
        == "postgresql+asyncpg://cyber%2Fteam:pg%2Fpass%40word%3Awith%23chars"
        "@localhost:5432/cyber%2Fteam"
    )
    assert (
        settings.redis_url
        == "redis://:redis%2Fpass%40word%3Awith%23chars@localhost:6379/0"
    )
