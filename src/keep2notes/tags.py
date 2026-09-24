"""Map Keep label names to Apple Notes tag names.

Apple Notes tags are a single "word": letters, numbers, hyphens, underscores. Spaces end a tag.
"""

from __future__ import annotations

import re
import unicodedata

_JOINERS = {"\u200d", "\ufe0f", "\ufe0e", "\u20e3"}


def _is_emoji(ch: str) -> bool:
    if ch in _JOINERS:
        return True
    cp = ord(ch)
    if 0x1F1E6 <= cp <= 0x1F1FF or 0x1F3FB <= cp <= 0x1F3FF:  # regional indicators, skin tones
        return True
    return unicodedata.category(ch) == "So"


def label_to_tag(label: str, strip_emoji: bool = False) -> str:
    text = unicodedata.normalize("NFC", label).strip()
    out = []
    for ch in text:
        if _is_emoji(ch):
            out.append("" if strip_emoji else ch)
        elif ch.isalnum() or ch in "_-":
            out.append(ch)
        else:
            out.append("-")
    tag = re.sub(r"-{2,}", "-", "".join(out)).strip("-")
    if tag:
        return tag
    if strip_emoji:
        names = [unicodedata.name(ch, "") for ch in text if ch not in _JOINERS]
        words = [n.split()[-1].lower() for n in names if n]
        return "-".join(words) or "label"
    return "label"


def labels_to_tags(labels: list[str], strip_emoji: bool = False) -> list[str]:
    seen: dict[str, None] = {}
    for label in labels:
        seen.setdefault(label_to_tag(label, strip_emoji), None)
    return list(seen)
