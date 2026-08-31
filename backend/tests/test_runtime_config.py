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
        "autonomy_side_effect_mode": "manual_only",
        "require_live_tool_executors": True,
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


def test_production_runtime_config_requires_manual_only_autonomy():
    settings = production_settings(autonomy_side_effect_mode="approval_required")

    with pytest.raises(RuntimeError, match="AUTONOMY_SIDE_EFFECT_MODE"):
        settings.validate_runtime_config()


def test_production_runtime_config_requires_live_tool_executors():
    settings = production_settings(require_live_tool_executors=False)

    with pytest.raises(RuntimeError, match="REQUIRE_LIVE_TOOL_EXECUTORS"):
        settings.validate_runtime_config()


def test_production_runtime_config_requires_hosted_llm_pacing():
    settings = production_settings(llm_hosted_pacing_enabled=False)

    with pytest.raises(RuntimeError, match="LLM_HOSTED_PACING_ENABLED"):
        settings.validate_runtime_config()


def test_production_runtime_config_requires_llm_recovery_probe():
    settings = production_settings(llm_recovery_probe_enabled=False)

    with pytest.raises(RuntimeError, match="LLM_RECOVERY_PROBE_ENABLED"):
        settings.validate_runtime_config()


def test_production_runtime_config_accepts_hardened_values():
    settings = production_settings()

    settings.validate_runtime_config()


def test_mistral_numbered_pool_is_authoritative_and_deduplicated():
    settings = Settings(
        mistral_api_key="legacy-key",
        mistral_api_key_1="pool-key-1",
        mistral_api_key_2="pool-key-2",
        mistral_api_key_3="pool-key-2",
        mistral_api_key_4="pool-key-4",
        mistral_api_key_5="pool-key-5",
    )

    assert settings.mistral_effective_api_keys == [
        "pool-key-1",
        "pool-key-2",
        "pool-key-4",
        "pool-key-5",
    ]
    assert settings.llm_effective_api_key == "pool-key-1"


def test_mistral_legacy_key_remains_single_key_fallback():
    settings = Settings(mistral_api_key="legacy-key")

    assert settings.mistral_effective_api_keys == ["legacy-key"]


def test_owner_notification_recipient_is_independent_with_login_fallback():
    separate = Settings(
        owner_email="login@example.com",
        owner_notification_email="alerts@example.net",
    )
    fallback = Settings(owner_email="login@example.com", owner_notification_email="")

    assert separate.owner_notification_recipient == "alerts@example.net"
    assert fallback.owner_notification_recipient == "login@example.com"


@pytest.mark.parametrize("required_count", [0, 6])
def test_production_runtime_config_rejects_invalid_credential_pool_size(required_count):
    settings = production_settings(llm_hosted_credential_required_count=required_count)

    with pytest.raises(RuntimeError, match="LLM_HOSTED_CREDENTIAL_REQUIRED_COUNT"):
        settings.validate_runtime_config()


def test_connection_urls_escape_reserved_characters():
    settings = Settings(
        postgres_user="cyber/team",
        postgres_password="pg/pass@word:with#chars",
        postgres_db="cyber/team",
        postgres_host="localhost",
        redis_password="redis/pass@word:with#chars",
        redis_host="localhost",
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
