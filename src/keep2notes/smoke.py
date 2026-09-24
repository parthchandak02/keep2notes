"""Synthetic Keep notes that each exercise one feature. Used by `keep2notes smoke` and the tests."""

from __future__ import annotations

import struct
import zlib
from datetime import datetime
from pathlib import Path

from .model import KeepNote, note_from_dict

BOLD = "font-weight:700;"
REGULAR = "font-weight:400;"


def _usec(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000)


def _p(text: str, style: str = REGULAR) -> str:
    return f'<p dir="ltr" style="line-height:1.38;"><span style="{style}white-space:pre-wrap;">{text}</span></p>'


def tiny_png(color=(220, 40, 40), size=24) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    row = b"\x00" + bytes(color) * size
    raw = zlib.compress(row * size)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def smoke_dicts() -> list[dict]:
    # 23:30 local time on 2019-01-01, to catch timezone mistakes.
    late = _usec(datetime(2019, 1, 1, 23, 30).astimezone())
    base = _usec(datetime(2024, 6, 1, 12, 0).astimezone())

    def note(title: str, offset: int = 0, **extra) -> dict:
        d = {
            "title": title,
            "color": "DEFAULT",
            "isTrashed": False,
            "isPinned": False,
            "isArchived": False,
            "createdTimestampUsec": base + offset * 60_000_000,
            "userEditedTimestampUsec": base + offset * 60_000_000 + 1_000_000,
        }
        d.update(extra)
        return d

    return [
        note(
            "K2N 1 Checklist",
            1,
            listContent=[
                {"text": "Unchecked item", "textHtml": _p("Unchecked item"), "isChecked": False},
                {"text": "Checked item", "textHtml": _p("Checked item"), "isChecked": True},
                {"text": "Bold item", "textHtml": _p("Bold item", BOLD), "isChecked": False},
            ],
        ),
        note(
            "K2N 2 Rich text",
            2,
            textContent="Heading\nNormal line\nBold line\n\nAfter blank line",
            textContentHtml="<h1>Heading</h1>" + _p("Normal line") + _p("Bold line", BOLD) + "<br />" + _p("After blank line"),
        ),
        note(
            "K2N 3 Image",
            3,
            textContent="Red square below.",
            attachments=[{"filePath": "k2n-smoke.png", "mimetype": "image/png"}],
        ),
        note(
            "K2N 4 Tags",
            4,
            textContent="Tags: emoji, spaces, double space, ampersand, hyphen.",
            labels=[
                {"name": "Gym 💪"},
                {"name": "Outdoors  🚵🎢🏂🏄"},
                {"name": "Movies & Shows 🎥🍿"},
                {"name": "self-care"},
                {"name": "🔒"},
            ],
        ),
        note(
            "K2N 5 Links",
            5,
            textContent="Inline link https://example.com/a?b=1&c=2 in text.",
            annotations=[
                {"url": "https://example.com/a?b=1&c=2", "title": "Already in text", "source": "WEBLINK"},
                {"url": "https://www.apple.com/", "title": "Apple", "source": "WEBLINK"},
            ],
        ),
        note("", 6, textContent="K2N 6 first line becomes title\nsecond line"),
        note(
            "K2N 7 Escaping <&> ]]>",
            7,
            textContent="Less < greater > amp & cdata-end ]]> quotes \"'\n  two leading spaces",
        ),
        {
            **note("K2N 8 Date 2019-01-01 23:30 local", textContent="Created date should read Jan 1 2019, 11:30 PM."),
            "createdTimestampUsec": late,
            "userEditedTimestampUsec": late,
        },
    ]


def smoke_notes(asset_dir: Path) -> list[KeepNote]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "k2n-smoke.png").write_bytes(tiny_png())
    return [note_from_dict(d, asset_dir, source=f"smoke-{i}") for i, d in enumerate(smoke_dicts(), 1)]
