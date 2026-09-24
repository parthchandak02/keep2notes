from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ListItem:
    text: str
    checked: bool
    html: str = ""


@dataclass
class Attachment:
    path: Path
    mime: str


@dataclass
class Annotation:
    url: str
    title: str = ""


@dataclass
class KeepNote:
    source: str
    title: str
    text: str
    html: str
    items: list[ListItem]
    labels: list[str]
    attachments: list[Attachment]
    annotations: list[Annotation]
    sharees: list[str]
    created: datetime
    updated: datetime
    archived: bool = False
    pinned: bool = False
    trashed: bool = False
    color: str = "DEFAULT"
    warnings: list[str] = field(default_factory=list)

    @property
    def is_checklist(self) -> bool:
        return bool(self.items)

    @property
    def is_empty(self) -> bool:
        return not (self.title or self.text.strip() or self.items or self.attachments)


def usec_to_dt(value) -> datetime:
    return datetime.fromtimestamp(int(value) / 1_000_000, tz=timezone.utc)


def _resolve_attachment(keep_dir: Path, file_path: str) -> Path | None:
    candidate = keep_dir / file_path
    if candidate.exists():
        return candidate
    # Takeout sometimes records .jpeg while the file on disk is .jpg (or vice versa).
    stem = Path(file_path).stem
    matches = [
        m for m in sorted(keep_dir.glob(f"{stem}.*")) if m.suffix.lower() not in {".json", ".html"}
    ]
    return matches[0] if matches else None


def note_from_dict(data: dict, keep_dir: Path, source: str = "") -> KeepNote:
    warnings: list[str] = []

    attachments = []
    for att in data.get("attachments", []):
        resolved = _resolve_attachment(keep_dir, att["filePath"])
        if resolved is None:
            warnings.append(f"missing attachment {att['filePath']}")
            continue
        attachments.append(Attachment(resolved, att.get("mimetype") or "application/octet-stream"))

    created_raw = data.get("createdTimestampUsec") or data.get("userEditedTimestampUsec")
    updated_raw = data.get("userEditedTimestampUsec") or created_raw
    if created_raw is None:
        raise ValueError(f"{source}: no timestamps")

    return KeepNote(
        source=source,
        title=(data.get("title") or "").strip(),
        text=data.get("textContent") or "",
        html=data.get("textContentHtml") or "",
        items=[
            ListItem(i.get("text", ""), bool(i.get("isChecked")), i.get("textHtml", ""))
            for i in data.get("listContent", [])
        ],
        labels=[lbl["name"] for lbl in data.get("labels", []) if lbl.get("name")],
        attachments=attachments,
        annotations=[
            Annotation(a["url"], a.get("title", "")) for a in data.get("annotations", []) if a.get("url")
        ],
        sharees=[s.get("email", "") for s in data.get("sharees", []) if not s.get("isOwner")],
        created=usec_to_dt(created_raw),
        updated=usec_to_dt(updated_raw),
        archived=bool(data.get("isArchived")),
        pinned=bool(data.get("isPinned")),
        trashed=bool(data.get("isTrashed")),
        color=data.get("color", "DEFAULT"),
        warnings=warnings,
    )


def load_keep_dir(keep_dir: Path) -> list[KeepNote]:
    notes = []
    for path in sorted(keep_dir.glob("*.json")):
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            continue
        if "createdTimestampUsec" not in data and "userEditedTimestampUsec" not in data:
            continue
        notes.append(note_from_dict(data, keep_dir, source=path.name))
    notes.sort(key=lambda n: n.created)
    return notes
