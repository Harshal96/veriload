"""Safety checks and redaction helpers for VeriLoad."""

from __future__ import annotations

import re
from pathlib import Path

from veriload.data import PersonaPool


class SafetyError(Exception):
    """Raised when a safety check fails."""


def assert_persona_pool_safe(
    pool: PersonaPool,
    *,
    require_non_routable_contacts: bool = True,
) -> None:
    """Validate generated personas before any traffic is sent."""

    if not require_non_routable_contacts:
        return
    for persona in pool.personas:
        if not _is_non_routable_email(persona.contact.email):
            raise SafetyError(
                f"Persona {persona.persona_id} contact.email must use a non-routable "
                "example.invalid domain"
            )


def stop_requested(stop_file: Path | None) -> bool:
    """Return whether the configured emergency stop file exists."""

    return stop_file is not None and stop_file.exists()


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization:\s*bearer\s+)([A-Za-z0-9._~+/=-]+)"),
    re.compile(r"(?i)\b(password\s*=\s*)([^\s&]+)"),
    re.compile(r"(?i)\b(api[_-]?key\s*=\s*)([^\s&]+)"),
    re.compile(r"(?i)\b(token\s*=\s*)([^\s&]+)"),
)


def redact_secrets(text: str) -> str:
    """Redact common token/password patterns from loggable strings."""

    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)
    return redacted


def _is_non_routable_email(email: str) -> bool:
    _, separator, domain = email.rpartition("@")
    if not separator:
        return False
    return domain == "example.invalid" or domain.endswith(".example.invalid")
