"""Map Keep label names to Apple Notes tag names.

Apple Notes tags are a single "word": a space ends the tag. Labels become PascalCase text with any
emoji moved to the end, e.g. "3D Printing 🤖" -> "3DPrinting🤖", "Movies & Shows 🎥🍿" -> "MoviesShows🎥🍿".
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
    emoji = "".join(ch for ch in text if _is_emoji(ch)).lstrip("".join(_JOINERS))
    plain = "".join(" " if _is_emoji(ch) else ch for ch in text)
    words = re.findall(r"[^\W_]+", plain)
    name = "".join(w[:1].upper() + w[1:] for w in words)
    tag = name if strip_emoji else name + emoji
    if tag:
        return tag
    if strip_emoji:
        names = [unicodedata.name(ch, "") for ch in text if ch not in _JOINERS]
        return "".join(n.split()[-1].capitalize() for n in names if n) or "Label"
    return "Label"


def labels_to_tags(labels: list[str], strip_emoji: bool = False) -> list[str]:
    seen: dict[str, None] = {}
    for label in labels:
        seen.setdefault(label_to_tag(label, strip_emoji), None)
    return list(seen)
