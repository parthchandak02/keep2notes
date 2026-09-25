"""Self-contained before/after HTML page for reviewing cleaned notes."""

from __future__ import annotations

import html
import re
from pathlib import Path

from .cleanup import OverlayReport, render_blocks
from .enex import note_title
from .model import KeepNote

CSS = """
body{font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0;background:#1e1e1e;color:#e6e6e6}
header{position:sticky;top:0;background:#111;padding:10px 20px;border-bottom:1px solid #333;z-index:1}
.note{border-bottom:1px solid #333;padding:16px 20px}
.note h2{font-size:15px;margin:0 0 6px}
.meta{color:#999;font-size:12px;margin-bottom:8px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.col{background:#262626;border-radius:8px;padding:10px 12px;overflow:auto;max-height:520px}
.col h3{margin:0 0 6px;font-size:12px;color:#aaa;text-transform:uppercase;letter-spacing:.05em}
pre{white-space:pre-wrap;margin:0;font:13px/1.45 ui-monospace,Menlo,monospace}
.todo{display:flex;gap:6px;align-items:baseline}
.box{width:12px;height:12px;border:1.5px solid #888;border-radius:50%;display:inline-block;flex:none}
.box.on{background:#e8b33b;border-color:#e8b33b}
.err{color:#ff7b72}.fix{color:#e8b33b}.add{color:#7ee787}
ul,ol{margin:2px 0;padding-left:20px}
"""


def _original(n: KeepNote) -> str:
    if n.items:
        return "\n".join(f"{'[x]' if i.checked else '[ ]'} {i.text}" for i in n.items if i.text.strip())
    return n.text


def _rendered(blocks: list[dict]) -> str:
    enml = render_blocks(blocks)
    enml = re.sub(
        r'<div><en-todo checked="(true|false)"/>(.*?)</div>',
        lambda m: f'<div class="todo"><span class="box{" on" if m.group(1) == "true" else ""}"></span><span>{m.group(2)}</span></div>',
        enml,
    )
    return enml


def write_review(path: Path, pairs: list[tuple[KeepNote, dict, OverlayReport]]) -> Path:
    ok = sum(r.ok for _, _, r in pairs)
    parts = [
        "<!doctype html><meta charset='utf-8'><title>keep2notes review</title>",
        f"<style>{CSS}</style>",
        f"<header><b>keep2notes cleanup review</b> - {len(pairs)} notes, {ok} pass, {len(pairs) - ok} rejected</header>",
    ]
    for n, ov, rep in pairs:
        title = html.escape(ov.get("title") or note_title(n))
        meta = [html.escape(n.source), "archived" if n.archived else "active", ", ".join(map(html.escape, n.labels))]
        notes = []
        if rep.errors:
            notes.append("<div class='err'>Rejected: " + html.escape("; ".join(rep.errors)) + "</div>")
        if rep.typo_fixes:
            notes.append(
                "<div class='fix'>Spelling: " + html.escape(", ".join(f"{a} -> {b}" for a, b in rep.typo_fixes)) + "</div>"
            )
        if rep.added:
            notes.append("<div class='add'>Added words: " + html.escape(", ".join(rep.added[:40])) + "</div>")
        if ov.get("changes"):
            notes.append("<div>Summary: " + html.escape(ov["changes"]) + "</div>")
        parts.append(
            f"<section class='note'><h2>{title}</h2><div class='meta'>{' | '.join(m for m in meta if m)}</div>"
            + "".join(notes)
            + "<div class='cols'>"
            + f"<div class='col'><h3>Before</h3><pre>{html.escape(n.title)}\n\n{html.escape(_original(n))}</pre></div>"
            + f"<div class='col'><h3>After</h3><b>{title}</b>{_rendered(ov['blocks']) if not rep.errors or ov.get('blocks') else ''}</div>"
            + "</div></section>"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(parts), encoding="utf-8")
    return path
