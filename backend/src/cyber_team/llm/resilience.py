"""Shared LLM provider error classification helpers."""

RETRYABLE_LLM_ERRORS = {"rate_limited", "timeout", "provider_unavailable"}


def classify_llm_exception(exc: Exception) -> str:
    """Return a stable, secret-free category for provider exceptions."""
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    status_code = getattr(exc, "status_code", None)
    if status_code == 429 or "ratelimit" in name or "rate limit" in message:
        return "rate_limited"
    if status_code in {401, 403} or "authentication" in name or "unauthorized" in message:
        return "authentication_error"
    if status_code in {408, 504} or "timeout" in name or "timed out" in message:
        return "timeout"
    if status_code in {500, 502, 503} or any(
        marker in name or marker in message
        for marker in ("serviceunavailable", "connection", "overloaded")
    ):
        return "provider_unavailable"
    if status_code in {400, 404, 409, 422} or "badrequest" in name:
        return "invalid_request"
    if "circuitopen" in name or "circuit breaker" in message:
        return "circuit_open"
    return "provider_error"


def classify_llm_trace_error(error: object) -> str | None:
    """Classify the legacy ``invoke:<type>:<message>`` trace representation."""
    value = str(error or "")
    if not value.startswith("invoke:"):
        return None
    _, _, detail = value.partition(":")
    exception_name, _, message = detail.partition(":")
    synthetic = type(exception_name or "ProviderError", (Exception,), {})
    return classify_llm_exception(synthetic(message))


def llm_error_is_retryable(category: str) -> bool:
    return category in RETRYABLE_LLM_ERRORS
