from __future__ import annotations

from hashlib import sha256

from ctxforge.context.models import ContextSection, PrefixSnapshot
from ctxforge.context.render import render_section


def hash_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def build_prefix_snapshot(stable_prefix: str, sections: list[ContextSection]) -> PrefixSnapshot:
    section_hashes = {
        section.name: hash_text(render_section(section))
        for section in sections
        if section.stability == "stable"
    }
    stable_prefix_bytes = stable_prefix.encode("utf-8")
    return PrefixSnapshot(
        stable_prefix_bytes=stable_prefix_bytes,
        stable_prefix_sha256=sha256(stable_prefix_bytes).hexdigest(),
        section_hashes=section_hashes,
    )
