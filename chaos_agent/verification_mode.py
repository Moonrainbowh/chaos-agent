from __future__ import annotations

import os


def structured_verification_enabled() -> bool:
    """Keep the structured verifier opt-in while it is unavailable in normal use."""
    return os.getenv("CHAOS_STRUCTURED_VERIFICATION", "0") == "1"
