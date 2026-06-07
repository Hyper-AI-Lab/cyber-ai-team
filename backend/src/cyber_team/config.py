"""Cyber-Team configuration via environment variables."""


from urllib.parse import quote

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "Cyber-Team"
    app_version: str = "0.1.0"
    build_sha: str = "local"
    environment: str = "development"
    log_level: str = "INFO"
    secret_key: str = "changeme-app-secret-key"
    cors_allowed_origins: str = "*"
    data_dir: str = "/app/data"

    # Owner
    owner_email: str = "owner@example.com"
    owner_password: str = "changeme-owner-password"
    owner_password_hash: str = ""
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    websocket_ticket_expire_seconds: int = 30
    rate_limit_login_per_minute: int = 10
    rate_limit_refresh_per_minute: int = 30
    rate_limit_websocket_ticket_per_minute: int = 30
    rate_limit_chat_per_minute: int = 60
    rate_limit_tool_execute_per_minute: int = 30
    rate_limit_approval_per_minute: int = 30
    communications_allow_simulation: bool = True
    communications_retry_attempts: int = 2
    communications_retry_backoff_seconds: float = 0.25
    communications_provider_timeout_seconds: float = 10.0
    communications_circuit_breaker_failure_threshold: int = 3
    communications_circuit_breaker_cooldown_seconds: int = 60
    retention_memory_days: int = 365
    retention_communication_log_days: int = 365
    retention_workflow_run_days: int = 180
    retention_approval_request_days: int = 730
    retention_audit_event_days: int = 2555
    retention_batch_size: int = 500
    autonomous_operations_enabled: bool = True
    autonomous_operations_initial_delay_seconds: int = 60
    autonomous_operations_interval_seconds: int = 900
    autonomous_planner_enabled: bool = True
    autonomous_planner_auto_execute_safe_tasks: bool = True
    autonomous_planner_scan_limit: int = 50
    autonomy_side_effect_mode: str = "approval_required"
    require_live_tool_executors: bool = False
    supervisor_review_enabled: bool = True
    supervisor_review_initial_delay_seconds: int = 60
    supervisor_review_interval_seconds: int = 900
    supervisor_review_stale_approval_hours: int = 24
    supervisor_review_failure_lookback_hours: int = 24
    supervisor_review_failure_threshold: int = 3
    memory_steward_enabled: bool = True
    memory_steward_initial_delay_seconds: int = 120
    memory_steward_interval_seconds: int = 1800
    memory_steward_trace_lookback_hours: int = 24
    memory_steward_trace_limit: int = 200
    memory_steward_empty_recall_threshold: int = 3
    memory_steward_stale_procedural_days: int = 30
    memory_steward_planner_enabled: bool = True
    memory_steward_auto_apply_safe_actions: bool = True
    memory_steward_request_action_approvals: bool = True

    # Mistral / LLM
    mistral_api_key: str = ""
    litellm_log: str = "INFO"
    llm_history_max_conversations: int = 100
    llm_history_max_messages: int = 20

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "cyberteam"
    postgres_user: str = "cyberteam"
    postgres_password: str = "changeme-postgres-password"
    database_migrations_on_startup: bool = True
    database_create_all_fallback: bool = False

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = "changeme-redis-password"

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_api_key: str = ""

    # Temporal
    temporal_host: str = "localhost"
    temporal_port: int = 7233
    temporal_namespace: str = "default"

    # Keycloak
    keycloak_host: str = "localhost"
    keycloak_port: int = 8080
    keycloak_admin: str = "admin"
    keycloak_admin_password: str = "changeme-keycloak-admin"
    keycloak_realm: str = "cyberteam"

    # OpenFGA
    openfga_api_url: str = "http://localhost:9090"

    # OPA
    opa_api_url: str = "http://localhost:8181"

    # ERPNext
    erpnext_url: str = "http://localhost:8100"
    erpnext_api_key: str = ""
    erpnext_api_secret: str = ""

    # Telephony
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    twilio_whatsapp_from_number: str = ""
    asterisk_ari_enabled: bool = False
    asterisk_ari_use_tls: bool = False
    asterisk_ari_app: str = "cyberteam"
    asterisk_ari_endpoint_template: str = "PJSIP/{to_number}"
    asterisk_caller_id: str = "Cyber-Team"
    asterisk_host: str = "localhost"
    asterisk_port: int = 8089
    asterisk_ari_user: str = "cyberteam"
    asterisk_ari_password: str = "changeme-ari-password"
    jasmin_host: str = "localhost"
    jasmin_port: int = 1401
    jasmin_username: str = ""
    jasmin_password: str = ""
    jasmin_from_number: str = ""
    jasmin_use_tls: bool = False

    # Messaging
    slack_webhook_url: str = ""
    telegram_bot_token: str = ""

    # Email
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_use_tls: bool = False
    smtp_starttls: bool = True
    inbound_email_enabled: bool = False
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_use_ssl: bool = True
    imap_mailbox: str = "INBOX"
    inbound_email_address: str = ""
    inbound_email_poll_interval_seconds: int = 60
    inbound_email_max_messages_per_poll: int = 20
    inbound_email_mark_seen: bool = False

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3100"

    @property
    def postgres_dsn(self) -> str:
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        database = quote(self.postgres_db, safe="")
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
        )

    @property
    def redis_url(self) -> str:
        password = quote(self.redis_password, safe="")
        return f"redis://:{password}@{self.redis_host}:{self.redis_port}/0"

    @property
    def qdrant_url(self) -> str:
        return f"http://{self.qdrant_host}:{self.qdrant_port}"

    @property
    def temporal_url(self) -> str:
        return f"{self.temporal_host}:{self.temporal_port}"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def cors_allows_wildcard(self) -> bool:
        return "*" in self.cors_origins

    def validate_runtime_config(self) -> None:
        if self.environment.lower() != "production":
            return
        insecure_values = {
            "SECRET_KEY": self.secret_key == "changeme-app-secret-key",
            "OWNER_PASSWORD_HASH": not self.owner_password_hash,
            "POSTGRES_PASSWORD": self.postgres_password == "changeme-postgres-password",
            "REDIS_PASSWORD": self.redis_password == "changeme-redis-password",
            "COMMUNICATIONS_ALLOW_SIMULATION": self.communications_allow_simulation,
            "AUTONOMY_SIDE_EFFECT_MODE": self.autonomy_side_effect_mode != "manual_only",
            "REQUIRE_LIVE_TOOL_EXECUTORS": not self.require_live_tool_executors,
        }
        invalid = [name for name, is_invalid in insecure_values.items() if is_invalid]
        if invalid:
            raise RuntimeError(
                f"Refusing production startup with insecure defaults: {', '.join(invalid)}"
            )
        if self.cors_allows_wildcard:
            raise RuntimeError("Refusing production startup with wildcard CORS_ALLOWED_ORIGINS")

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
