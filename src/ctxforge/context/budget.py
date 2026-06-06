from __future__ import annotations

from math import ceil


def estimate_tokens(text: str) -> int:
    """Return a deterministic local estimate, not provider billing tokens."""
    if not text:
        return 0
    return max(1, ceil(len(text) / 4))
