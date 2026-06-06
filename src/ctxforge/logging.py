from __future__ import annotations

import logging
from typing import TextIO


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Configure stdlib logging once for CLI usage."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    try:
        from rich.logging import RichHandler

        handler: logging.Handler = RichHandler(rich_tracebacks=True, show_time=False)
    except Exception:
        handler = logging.StreamHandler(stream)

    logging.basicConfig(
        level=numeric_level,
        format="%(message)s",
        handlers=[handler],
        force=True,
    )
