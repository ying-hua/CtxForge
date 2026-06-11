from __future__ import annotations

from textual.message import Message

from ctxforge.runtime.events import RuntimeEvent


class RuntimeEventMessage(Message):
    def __init__(self, event: RuntimeEvent) -> None:
        self.event = event
        super().__init__()
