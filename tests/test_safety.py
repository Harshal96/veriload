from dataclasses import replace

import pytest

from veriload.data import Contact, PersonaPool
from veriload.safety import SafetyError, assert_persona_pool_safe, redact_secrets


def test_assert_persona_pool_safe_accepts_example_invalid_contacts(sample_persona) -> None:
    assert_persona_pool_safe(PersonaPool([sample_persona]))


def test_assert_persona_pool_safe_rejects_routable_email(sample_persona) -> None:
    unsafe_persona = replace(
        sample_persona,
        contact=Contact(email="ada@gmail.com", phone_e164="+15550000001"),
    )

    with pytest.raises(SafetyError, match="non-routable"):
        assert_persona_pool_safe(PersonaPool([unsafe_persona]))


def test_redact_secrets_masks_common_token_patterns() -> None:
    text = "Authorization: Bearer abc123 password=hunter2 api_key=secret"

    redacted = redact_secrets(text)

    assert "abc123" not in redacted
    assert "hunter2" not in redacted
    assert "secret" not in redacted
    assert "[REDACTED]" in redacted
