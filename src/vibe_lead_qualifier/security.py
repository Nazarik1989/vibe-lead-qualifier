"""Webhook authentication helpers."""

from __future__ import annotations

import hashlib
import hmac


def make_signature(raw_body: bytes, webhook_secret: str) -> str:
    """Return the lowercase hexadecimal signature documented by VibeMarketolog."""

    return hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_signature(raw_body: bytes, webhook_secret: str, supplied_signature: str) -> bool:
    """Compare a webhook signature in constant time.

    The current Agent API uses a dedicated ``webhook_secret``. Legacy token-derived
    secrets are intentionally not derived by this service because the raw token is
    a separate credential and should not be repurposed implicitly.
    """

    if len(supplied_signature) != 64:
        return False
    expected = make_signature(raw_body, webhook_secret)
    return hmac.compare_digest(expected, supplied_signature.lower())
