"""Read-only comparison of imported Apple Notes against the Keep export, via AppleScript."""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass

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


def compare_titles(expected: list[str], found: list[str]) -> VerifyResult:
    exp = Counter(_key(t) for t in expected)
    got = Counter(_key(t) for t in found)
    by_key = {_key(t): t for t in expected} | {_key(t): t for t in found}
    missing = sorted(by_key[k] for k in (exp - got).elements())
    extra = sorted(by_key[k] for k in (got - exp).elements())
    return VerifyResult(len(expected), len(found), missing, extra)
