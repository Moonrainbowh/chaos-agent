from __future__ import annotations

import os


def structured_verification_enabled() -> bool:
    """Use risk-adapted Host verification unless explicitly disabled for compatibility."""
    return os.getenv("CHAOS_STRUCTURED_VERIFICATION", "1") == "1"
