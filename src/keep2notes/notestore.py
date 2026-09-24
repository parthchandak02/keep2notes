"""Read-only access to Apple Notes' NoteStore.sqlite for deep verification.

Needs Full Disk Access for the process running it. The database (plus WAL files) is copied to a
temp dir first, so the live store is never opened or locked.
"""

from __future__ import annotations

import gzip
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOTESTORE = Path.home() / "Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"
CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
HASHTAG_UTI = "com.apple.notes.inlinetextattachment.hashtag"
CHECKBOX_STYLE = 103


@dataclass
class StoredNote:
    title: str
    created: datetime | None
    modified: datetime | None
    tags: list[str] = field(default_factory=list)
    attachments: int = 0
    checked: int = 0
    unchecked: int = 0


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes):
    """Yield (field_number, wire_type, value) for one protobuf message."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _varint(buf, i)
        elif wire == 2:
            size, i = _varint(buf, i)
            val, i = buf[i : i + size], i + size
        elif wire == 1:
            val, i = buf[i : i + 8], i + 8
        elif wire == 5:
            val, i = buf[i : i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        yield num, wire, val


def _first(buf: bytes, num: int) -> bytes | None:
    return next((v for n, w, v in _fields(buf) if n == num and w == 2), None)


def checklist_counts(zdata: bytes) -> tuple[int, int]:
    """Return (checked, unchecked) checklist items from a note's gzipped protobuf body."""
    raw = gzip.decompress(zdata) if zdata[:2] == b"\x1f\x8b" else zdata
    document = _first(raw, 2)
    note = _first(document, 3) if document else None
    if note is None:
        return 0, 0
    items: dict[bytes, int] = {}
    for num, wire, run in _fields(note):
        if num != 5 or wire != 2:
            continue
        style = _first(run, 2)
        if style is None:
            continue
        style_type = next((v for n, w, v in _fields(style) if n == 1 and w == 0), None)
        checklist = _first(style, 5)
        if style_type != CHECKBOX_STYLE or checklist is None:
            continue
        uuid = _first(checklist, 1) or b""
        done = next((v for n, w, v in _fields(checklist) if n == 2 and w == 0), 0)
        items[uuid] = done
    checked = sum(1 for d in items.values() if d)
    return checked, len(items) - checked


def _core_date(value) -> datetime | None:
    return CORE_DATA_EPOCH + timedelta(seconds=value) if value is not None else None


def _copy_store(src: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="keep2notes-"))
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(src) + suffix)
        if p.exists():
            shutil.copy2(p, tmp / (src.name + suffix))
    return tmp / src.name


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def read_folder(account: str, folder: str, store: Path = NOTESTORE) -> list[StoredNote]:
    if not store.exists():
        raise FileNotFoundError(store)
    try:
        copy = _copy_store(store)
    except PermissionError as e:
        raise PermissionError("Grant Full Disk Access to the app running keep2notes") from e
    db = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    try:
        cols = _columns(db, "ZICCLOUDSYNCINGOBJECT")
        created_col = next(c for c in ("ZCREATIONDATE3", "ZCREATIONDATE1", "ZCREATIONDATE") if c in cols)
        modified_col = next(c for c in ("ZMODIFICATIONDATE1", "ZMODIFICATIONDATE") if c in cols)
        note_fk = [c for c in ("ZNOTE", "ZNOTE1") if c in cols]
        uti_cols = ", ".join(f"a.{c}" for c in ("ZTYPEUTI", "ZTYPEUTI1") if c in cols)

        folder_ids = [
            r[0]
            for r in db.execute(
                "SELECT f.Z_PK FROM ZICCLOUDSYNCINGOBJECT f JOIN ZICCLOUDSYNCINGOBJECT a ON a.Z_PK = f.ZOWNER "
                "WHERE f.ZTITLE2 = ? AND a.ZNAME = ?",
                (folder, account),
            )
        ]
        if not folder_ids:
            raise LookupError(f"folder {folder!r} not found in account {account!r}")

        rows = db.execute(
            f"SELECT n.Z_PK, n.ZTITLE1, n.{created_col}, n.{modified_col}, d.ZDATA "
            "FROM ZICCLOUDSYNCINGOBJECT n LEFT JOIN ZICNOTEDATA d ON d.Z_PK = n.ZNOTEDATA "
            f"WHERE n.ZFOLDER IN ({','.join('?' * len(folder_ids))}) "
            "AND n.ZTITLE1 IS NOT NULL AND coalesce(n.ZMARKEDFORDELETION, 0) = 0",
            folder_ids,
        ).fetchall()

        fk = f"coalesce({', '.join('a.' + c for c in note_fk)})" if len(note_fk) > 1 else f"a.{note_fk[0]}"
        notes = []
        for pk, title, created, modified, zdata in rows:
            children = db.execute(
                f"SELECT {uti_cols}, a.ZALTTEXT FROM ZICCLOUDSYNCINGOBJECT a WHERE {fk} = ? "
                "AND coalesce(a.ZMARKEDFORDELETION, 0) = 0",
                (pk,),
            ).fetchall()
            # Inline attachments (hashtags, mentions) and file attachments use different UTI columns.
            utis = [(next((u for u in row[:-1] if u), None), row[-1]) for row in children]
            tags = sorted(alt.lstrip("#") for uti, alt in utis if uti == HASHTAG_UTI and alt)
            attachments = sum(
                1 for uti, _ in utis if uti and not uti.startswith("com.apple.notes.inlinetextattachment")
            )
            checked, unchecked = checklist_counts(zdata) if zdata else (0, 0)
            notes.append(
                StoredNote(title, _core_date(created), _core_date(modified), tags, attachments, checked, unchecked)
            )
        return notes
    finally:
        db.close()
        shutil.rmtree(copy.parent, ignore_errors=True)
