"""Read-only comparison of imported Apple Notes against the Keep export, via AppleScript."""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass

from .enex import enex_date
from .html_clean import normalize_ws

_SEP = "\u241e"


def _osascript(script: str) -> str:
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "osascript failed")
    return result.stdout.rstrip("\n")


def _as_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def list_folders(account: str) -> list[str]:
    out = _osascript(
        f'set AppleScript\'s text item delimiters to "{_SEP}"\n'
        f'tell application "Notes" to set t to name of every folder of account {_as_string(account)}\n'
        "return t as text"
    )
    return [f for f in out.split(_SEP) if f]


def note_titles(account: str, folder: str) -> list[str]:
    script = (
        f'set AppleScript\'s text item delimiters to "{_SEP}"\n'
        f'tell application "Notes" to set t to name of every note of folder {_as_string(folder)} '
        f"of account {_as_string(account)}\n"
        "return t as text"
    )
    out = _osascript(script)
    return [t for t in out.split(_SEP) if t] if out else []


@dataclass
class VerifyResult:
    expected: int
    found: int
    missing: list[str]
    extra: list[str]

    @property
    def ok(self) -> bool:
        return self.expected == self.found and not self.missing and not self.extra


def _key(title: str) -> str:
    # Notes derives a display name from the first line and may trim it, so compare loosely.
    return normalize_ws(title).rstrip("…").casefold()[:60]


@dataclass
class Expected:
    title: str
    created: str
    updated: str
    tags: list[str]
    resources: int
    checked: int
    unchecked: int


def expected_from_enex(paths) -> list[Expected]:
    out = []
    for p in paths:
        for n in ET.parse(p).getroot().findall("note"):
            content = n.findtext("content") or ""
            out.append(
                Expected(
                    title=n.findtext("title") or "",
                    created=n.findtext("created") or "",
                    updated=n.findtext("updated") or "",
                    tags=sorted(t.text or "" for t in n.findall("tag")),
                    resources=len(n.findall("resource")),
                    checked=content.count('<en-todo checked="true"/>'),
                    unchecked=content.count('<en-todo checked="false"/>'),
                )
            )
    return out


def deep_compare(expected: list[Expected], stored) -> list[tuple[str, list[str]]]:
    """Match notes by title (then creation time for duplicates) and list per-note differences."""
    pool: dict[str, list] = {}
    for s in stored:
        pool.setdefault(_key(s.title), []).append(s)
    problems = []
    for e in sorted(expected, key=lambda x: x.created):
        candidates = pool.get(_key(e.title), [])
        if not candidates:
            problems.append((e.title, ["missing in Notes"]))
            continue
        match = next((s for s in candidates if s.created and enex_date(s.created) == e.created), candidates[0])
        candidates.remove(match)
        diffs = []
        if not match.created or enex_date(match.created) != e.created:
            diffs.append(f"created {e.created} vs {match.created and enex_date(match.created)}")
        if not match.modified or enex_date(match.modified) != e.updated:
            diffs.append(f"modified {e.updated} vs {match.modified and enex_date(match.modified)}")
        if sorted(match.tags) != e.tags:
            diffs.append(f"tags {e.tags} vs {sorted(match.tags)}")
        if match.attachments < e.resources:
            diffs.append(f"images {e.resources} vs {match.attachments}")
        if (match.checked, match.unchecked) != (e.checked, e.unchecked):
            diffs.append(f"checklist {e.checked}/{e.unchecked} vs {match.checked}/{match.unchecked} (checked/unchecked)")
        if diffs:
            problems.append((e.title, diffs))
    for leftover in (s for group in pool.values() for s in group):
        problems.append((leftover.title, ["extra note in Notes"]))
    return problems


def compare_titles(expected: list[str], found: list[str]) -> VerifyResult:
    exp = Counter(_key(t) for t in expected)
    got = Counter(_key(t) for t in found)
    by_key = {_key(t): t for t in expected} | {_key(t): t for t in found}
    missing = sorted(by_key[k] for k in (exp - got).elements())
    extra = sorted(by_key[k] for k in (got - exp).elements())
    return VerifyResult(len(expected), len(found), missing, extra)
