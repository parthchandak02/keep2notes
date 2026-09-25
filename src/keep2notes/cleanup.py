"""Cleaned-note overlays: chunk export for editors, rendering to ENML, and safety validation.

An overlay replaces a note's title and body, never its labels, dates, images, or archive state:

    {"source": "Trip.json", "title": "Trip", "blocks": [
        {"type": "heading", "text": "Documents"},
        {"type": "check", "text": "Passport", "checked": false},
        {"type": "bullet", "text": "Snacks"},
        {"type": "number", "text": "Step one"},
        {"type": "text", "text": "Plain paragraph with **bold**"},
        {"type": "blank"}
    ], "changes": "one-line summary"}
"""

from __future__ import annotations

import difflib
import html
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .html_clean import text_to_enml
from .model import KeepNote

BLOCK_TYPES = {"heading", "text", "bullet", "number", "check", "blank"}
_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_LIST_NUM_RE = re.compile(r"^[ \t]*\d{1,3}[.)][ \t]+", re.M)


def overlay_path(overlay_dir: Path, source: str) -> Path:
    return overlay_dir / source


def load_overlays(overlay_dir: Path) -> dict[str, dict]:
    overlays = {}
    for p in sorted(overlay_dir.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        overlays[data.get("source") or p.name] = data
    return overlays


def _inline(text: str) -> str:
    parts, pos = [], 0
    for m in _BOLD_RE.finditer(text):
        parts.append(text_to_enml(text[pos : m.start()]))
        parts.append(f"<b>{text_to_enml(m.group(1))}</b>")
        pos = m.end()
    parts.append(text_to_enml(text[pos:]))
    return "".join(parts)


def render_blocks(blocks: list[dict]) -> str:
    out: list[str] = []
    open_list: str | None = None
    for b in blocks:
        kind = b.get("type")
        list_tag = {"bullet": "ul", "number": "ol"}.get(kind)
        if open_list and list_tag != open_list:
            out.append(f"</{open_list}>")
            open_list = None
        raw = b.get("text", "")
        if kind == "number":
            raw = _LIST_NUM_RE.sub("", raw, count=1)
        text = _inline(raw)
        if list_tag:
            if not open_list:
                out.append(f"<{list_tag}>")
                open_list = list_tag
            out.append(f"<li>{text}</li>")
        elif kind == "check":
            checked = "true" if b.get("checked") else "false"
            out.append(f'<div><en-todo checked="{checked}"/>{text}</div>')
        elif kind == "heading":
            out.append(f"<div><b>{text}</b></div>")
        elif kind == "blank":
            out.append("<div><br/></div>")
        else:
            out.append(f"<div>{text or '<br/>'}</div>")
    if open_list:
        out.append(f"</{open_list}>")
    return "".join(out)


# ---------- chunk export ----------


def note_payload(n: KeepNote) -> dict:
    d = {
        "source": n.source,
        "title": n.title,
        "labels": n.labels,
        "archived": n.archived,
        "has_images": bool(n.attachments),
    }
    if n.items:
        d["items"] = [{"text": i.text, "checked": i.checked} for i in n.items if i.text.strip()]
    else:
        d["text"] = n.text
    if n.html and "font-weight:700" in n.html:
        bold = re.findall(r"font-weight:700[^>]*>([^<]+)<", n.html)
        d["bold_phrases"] = [html.unescape(b).strip() for b in bold if b.strip()]
    return d


def write_chunks(notes: list[KeepNote], out_dir: Path, n_chunks: int, max_chars: int = 6000) -> list[Path]:
    """Split notes into roughly equal-size chunks by character count; very long notes get their own chunk."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sized = [(note_payload(n), len(n.text) + sum(len(i.text) for i in n.items) + 200) for n in notes]
    big = [p for p, s in sized if s > max_chars]
    small = [(p, s) for p, s in sized if s <= max_chars]
    buckets: list[list[dict]] = [[] for _ in range(max(n_chunks, 1))]
    loads = [0] * len(buckets)
    for p, s in sorted(small, key=lambda x: -x[1]):
        i = loads.index(min(loads))
        buckets[i].append(p)
        loads[i] += s
    buckets += [[p] for p in big]
    paths = []
    for i, bucket in enumerate((b for b in buckets if b), 1):
        path = out_dir / f"chunk-{i:02d}.json"
        path.write_text(json.dumps(bucket, ensure_ascii=False, indent=1), encoding="utf-8")
        paths.append(path)
    return paths


# ---------- validation ----------


def _words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).replace("’", "'")
    return [w.casefold() for w in _WORD_RE.findall(text)]


def _norm(text: str) -> str:
    return " ".join(_words(text))


def _hashtag_line_words(note: KeepNote) -> set[str]:
    """Words on lines made only of #hashtags (typed pseudo-tags) that may be removed."""
    exempt: set[str] = set()
    for line in note.text.splitlines():
        s = line.strip()
        if s and all(tok.startswith("#") or not _words(tok) for tok in s.split()):
            if "#" in s:
                exempt.update(_words(s))
    return exempt


@dataclass
class OverlayReport:
    source: str
    errors: list[str] = field(default_factory=list)
    typo_fixes: list[tuple[str, str]] = field(default_factory=list)
    added: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_overlay(note: KeepNote, overlay: dict) -> OverlayReport:
    rep = OverlayReport(note.source)
    blocks = overlay.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        rep.errors.append("no blocks")
        return rep
    for i, b in enumerate(blocks):
        if not isinstance(b, dict) or b.get("type") not in BLOCK_TYPES:
            rep.errors.append(f"block {i}: bad type {b!r:.60}")
        elif b["type"] != "blank" and not isinstance(b.get("text"), str):
            rep.errors.append(f"block {i}: missing text")
    if rep.errors:
        return rep

    new_title = overlay.get("title", note.title) or ""
    new_text = new_title + "\n" + "\n".join(_BOLD_RE.sub(r"\1", b.get("text", "")) for b in blocks)
    body = _LIST_NUM_RE.sub("", note.text) if any(b["type"] == "number" for b in blocks) else note.text
    old_text = note.title + "\n" + body + "\n" + "\n".join(i.text for i in note.items)
    new_words = set(_words(new_text))
    exempt = _hashtag_line_words(note)
    new_list = sorted(new_words)
    for w in dict.fromkeys(_words(old_text)):
        if w in new_words or w in exempt:
            continue
        close = difflib.get_close_matches(w, new_list, n=1, cutoff=0.75)
        if not close and len(w) > 2:
            close = [c for c in new_list if sorted(c) == sorted(w)][:1]
        if close and len(w) > 2:
            rep.typo_fixes.append((w, close[0]))
        else:
            rep.errors.append(f"dropped word: {w!r}")
    old_words = set(_words(old_text))
    rep.added = [w for w in dict.fromkeys(_words(new_text)) if w not in old_words and not any(w == t for _, t in rep.typo_fixes)]

    checks = [b for b in blocks if b["type"] == "check"]
    for item in (i for i in note.items if i.text.strip()):
        target = _norm(item.text)
        best = max(checks, key=lambda b: difflib.SequenceMatcher(None, target, _norm(b["text"])).ratio(), default=None)
        score = difflib.SequenceMatcher(None, target, _norm(best["text"])).ratio() if best else 0
        if score < 0.7:
            rep.errors.append(f"checklist item lost: {item.text[:50]!r}")
        elif bool(best.get("checked")) != item.checked:
            rep.errors.append(f"checked state changed: {item.text[:50]!r}")
    return rep
