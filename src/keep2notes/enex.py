"""Build Evernote ENEX files in the subset Apple Notes' importer understands."""

from __future__ import annotations

import base64
import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from . import __version__
from .cleanup import render_blocks
from .html_clean import html_to_enml, inline_html_to_enml, plain_to_enml, text_to_enml
from .model import KeepNote
from .tags import labels_to_tags

ENML_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'
)
TITLE_MAX = 255


@dataclass
class Options:
    strip_emoji_tags: bool = False
    pinned_tag: str | None = "Pinned"
    include_links: bool = True


@dataclass
class Resource:
    data: bytes
    mime: str
    file_name: str

    @property
    def md5(self) -> str:
        return hashlib.md5(self.data).hexdigest()


def enex_date(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def note_title(note: KeepNote) -> str:
    if note.overlay and note.overlay.get("title"):
        return note.overlay["title"].strip()[:TITLE_MAX]
    if note.title:
        return note.title[:TITLE_MAX]
    lines = [line.strip() for line in note.text.splitlines() if line.strip()]
    lines += [i.text.strip() for i in note.items if i.text.strip()]
    if lines:
        first = lines[0]
        return first if len(first) <= 80 else first[:79].rstrip() + "…"
    return f"Untitled {note.created.astimezone().strftime('%Y-%m-%d')}"


def note_tags(note: KeepNote, opts: Options) -> list[str]:
    tags = labels_to_tags(note.labels, opts.strip_emoji_tags)
    if note.pinned and opts.pinned_tag and opts.pinned_tag not in tags:
        tags.append(opts.pinned_tag)
    return tags


def note_resources(note: KeepNote) -> list[Resource]:
    seen: dict[str, Resource] = {}
    for att in note.attachments:
        res = Resource(att.path.read_bytes(), att.mime, att.path.name)
        seen.setdefault(res.md5, res)
    return list(seen.values())


def note_body(note: KeepNote, resources: list[Resource], opts: Options) -> str:
    parts: list[str] = []
    if note.overlay:
        parts.append(render_blocks(note.overlay["blocks"]))
    elif note.items:
        for item in note.items:
            content = inline_html_to_enml(item.html) if item.html else text_to_enml(item.text)
            if not item.text.strip() and not content.strip():
                continue
            checked = "true" if item.checked else "false"
            parts.append(f'<div><en-todo checked="{checked}"/>{content}</div>')
    elif note.html:
        parts.append(html_to_enml(note.html))
    elif note.text:
        parts.append(plain_to_enml(note.text))

    for res in resources:
        parts.append(f"<div><en-media type={quoteattr(res.mime)} hash=\"{res.md5}\"/></div>")

    if opts.include_links:
        haystack = note.text + "\n" + "\n".join(i.text for i in note.items)
        if note.overlay:
            haystack += "\n" + "\n".join(b.get("text", "") for b in note.overlay["blocks"])
        links = [a for a in note.annotations if a.url not in haystack]
        if links:
            parts.append("<div><br/></div><div><b>Links</b></div>")
            for a in links:
                label = a.title.strip() or a.url
                parts.append(f"<div><a href={quoteattr(a.url)}>{escape(label)}</a></div>")

    return "".join(parts) or "<div><br/></div>"


def _cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def note_xml(note: KeepNote, opts: Options | None = None) -> str:
    opts = opts or Options()
    resources = note_resources(note)
    en_note = f"<en-note>{note_body(note, resources, opts)}</en-note>"
    ET.fromstring(en_note)  # raises ParseError if the body is not well-formed

    out = [
        "<note>",
        f"<title>{escape(note_title(note))}</title>",
        f"<content>{_cdata(ENML_HEADER + en_note)}</content>",
        f"<created>{enex_date(note.created)}</created>",
        f"<updated>{enex_date(note.updated)}</updated>",
    ]
    out += [f"<tag>{escape(t)}</tag>" for t in note_tags(note, opts)]
    out.append("<note-attributes><source>google-keep</source></note-attributes>")
    for res in resources:
        out.append(
            "<resource>"
            f'<data encoding="base64">{base64.b64encode(res.data).decode("ascii")}</data>'
            f"<mime>{escape(res.mime)}</mime>"
            f"<resource-attributes><file-name>{escape(res.file_name)}</file-name></resource-attributes>"
            "</resource>"
        )
    out.append("</note>")
    return "".join(out)


def enex_document(notes: list[KeepNote], opts: Options | None = None) -> str:
    stamp = enex_date(datetime.now(timezone.utc))
    body = "\n".join(note_xml(n, opts) for n in notes)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE en-export SYSTEM "http://xml.evernote.com/pub/evernote-export4.dtd">\n'
        f'<en-export export-date="{stamp}" application="keep2notes" version="{__version__}">\n'
        f"{body}\n</en-export>\n"
    )


def write_enex(notes: list[KeepNote], path: Path, opts: Options | None = None) -> Path:
    doc = enex_document(notes, opts)
    root = ET.fromstring(doc.encode("utf-8"))
    if len(root.findall("note")) != len(notes):
        raise ValueError(f"{path.name}: note count mismatch after serialization")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
    return path
