import re


REDACTED = "<redacted>"
SENSITIVE_KEYS = {
    "access_token", "refresh_token", "password", "password_hash", "authorization",
    "cookie", "cookies", "session", "session_id", "session_secret", "client_secret",
    "api_key", "apikey", "secret", "token",
}
TEXT_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+"),
    re.compile(
        r"(?i)((?:access_token|refresh_token|password|client_secret|api_key|apikey|"
        r"session_secret|cookie|token)\s*[=:]\s*)[^\s,;&]+"
    ),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/]+=*"),
)


def _sensitive_key(key):
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS or any(
        marker in normalized for marker in ("password", "secret", "token", "api_key", "apikey")
    )


def redact_text(value):
    result = str(value)
    for pattern in TEXT_PATTERNS:
        result = pattern.sub(lambda match: f"{match.group(1)}{REDACTED}", result)
    return result


def redact(value):
    if isinstance(value, dict):
        return {
            str(key): REDACTED if _sensitive_key(key) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
