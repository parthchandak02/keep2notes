"""Convert Google Keep Takeout exports into ENEX files for Apple Notes."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .enex import Options, note_body, note_resources, note_tags, note_title, write_enex
from .html_clean import enml_to_text, html_to_enml, normalize_ws
from .model import KeepNote, load_keep_dir
from .smoke import smoke_notes
from .tags import label_to_tag
from .verify import compare_titles, list_folders, note_titles

console = Console()


def _chunks(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)] or []


def pick_pilot(notes: list[KeepNote], limit: int) -> list[KeepNote]:
    checks = [
        ("mixed checklist", lambda n: any(i.checked for i in n.items) and any(not i.checked for i in n.items)),
        ("bold text", lambda n: "font-weight:700" in n.html),
        ("heading", lambda n: "<h1" in n.html or "<h2" in n.html),
        ("image", lambda n: bool(n.attachments)),
        ("multi-label", lambda n: len(n.labels) > 1),
        ("label with double space", lambda n: any("  " in lbl for lbl in n.labels)),
        ("link annotation", lambda n: bool(n.annotations)),
        ("empty title", lambda n: not n.title and not n.is_empty),
        ("archived", lambda n: n.archived),
        ("pinned", lambda n: n.pinned),
        ("longest text", None),
    ]
    picked: list[KeepNote] = []
    for _, check in checks:
        if len(picked) >= limit:
            break
        pool = [n for n in notes if n not in picked]
        if check is None:
            pool.sort(key=lambda n: len(n.text), reverse=True)
            match = pool[:1]
        else:
            match = [n for n in pool if check(n)][:1]
        picked.extend(match)
    return picked


def text_mismatches(notes: list[KeepNote]) -> list[str]:
    bad = []
    for n in notes:
        if n.html and n.text and enml_to_text(html_to_enml(n.html)) != normalize_ws(n.text):
            bad.append(n.source)
    return bad


def summary_table(notes: list[KeepNote], opts: Options) -> Table:
    tags = {t for n in notes for t in note_tags(n, opts)}
    t = Table(title="keep2notes summary", show_header=True)
    t.add_column("metric")
    t.add_column("count", justify="right")
    rows = [
        ("notes", len(notes)),
        ("active", sum(not n.archived for n in notes)),
        ("archived", sum(n.archived for n in notes)),
        ("checklists", sum(n.is_checklist for n in notes)),
        ("checklist items", sum(len(n.items) for n in notes)),
        ("checked items", sum(i.checked for n in notes for i in n.items)),
        ("rich-text notes", sum(bool(n.html) for n in notes)),
        ("images", sum(len(n.attachments) for n in notes)),
        ("notes with links", sum(bool(n.annotations) for n in notes)),
        ("distinct tags", len(tags)),
        ("pinned", sum(n.pinned for n in notes)),
        ("shared", sum(bool(n.sharees) for n in notes)),
        ("empty", sum(n.is_empty for n in notes)),
    ]
    for k, v in rows:
        t.add_row(k, str(v))
    return t


def write_report(path: Path, notes: list[KeepNote], opts: Options, files: list[Path], mismatches: list[str]) -> None:
    lines = ["keep2notes report", ""]
    lines.append("Files:")
    lines += [f"  {f.name}" for f in files]
    lines += ["", "Tag mapping (Keep label -> Apple Notes tag):"]
    labels = sorted({lbl for n in notes for lbl in n.labels})
    lines += [f"  {lbl!r} -> #{label_to_tag(lbl, opts.strip_emoji_tags)}" for lbl in labels]
    lines += ["", "Pinned (pin these by hand, then remove the tag):"]
    lines += [f"  {note_title(n)}" for n in notes if n.pinned]
    lines += ["", "Shared in Keep (sharing does not carry over):"]
    lines += [f"  {note_title(n)}  [{', '.join(n.sharees)}]" for n in notes if n.sharees]
    lines += ["", "Rich text differs from Keep plain text (spot-check these):"]
    lines += [f"  {s}" for s in mismatches] or ["  none"]
    warn = [(n.source, w) for n in notes for w in n.warnings]
    lines += ["", "Warnings:"]
    lines += [f"  {s}: {w}" for s, w in warn] or ["  none"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_convert(args: argparse.Namespace) -> int:
    keep_dir = Path(args.keep_dir).expanduser()
    out = Path(args.out).expanduser()
    opts = Options(strip_emoji_tags=args.strip_emoji_tags)
    notes = load_keep_dir(keep_dir)
    trashed = [n for n in notes if n.trashed]
    if not args.include_trashed:
        notes = [n for n in notes if not n.trashed]

    files: list[Path] = []
    if args.pilot:
        pilot = pick_pilot(notes, args.pilot)
        files.append(write_enex(pilot, out / "Pilot.enex", opts))
        console.print(f"[bold]Pilot[/] {len(pilot)} notes -> {files[0]}")
        for n in pilot:
            console.print(f"  - {note_title(n)}")
        return 0

    for name, group in (("Active", [n for n in notes if not n.archived]), ("Archived", [n for n in notes if n.archived])):
        for i, chunk in enumerate(_chunks(group, args.batch_size), 1):
            files.append(write_enex(chunk, out / f"{name}-{i:02d}.enex", opts))

    mismatches = text_mismatches(notes)
    write_report(out / "report.txt", notes, opts, files, mismatches)
    console.print(summary_table(notes, opts))
    if trashed and not args.include_trashed:
        console.print(f"[yellow]Skipped {len(trashed)} trashed notes[/]")
    for f in files:
        console.print(f"  {f}")
    console.print(f"[green]Report:[/] {out / 'report.txt'}  ({len(mismatches)} rich-text diffs to spot-check)")
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    out = Path(args.out).expanduser()
    opts = Options(strip_emoji_tags=args.strip_emoji_tags)
    notes = smoke_notes(out / "smoke-assets")
    path = write_enex(notes, out / "Smoke.enex", opts)
    console.print(f"[bold]Smoke[/] {len(notes)} notes -> {path}")
    for n in notes:
        console.print(f"  - {note_title(n)}  tags={note_tags(n, opts)}")
    return 0


def _titles_from_enex(paths: list[Path]) -> list[str]:
    titles = []
    for p in paths:
        root = ET.parse(p).getroot()
        titles += [(n.findtext("title") or "") for n in root.findall("note")]
    return titles


def cmd_verify(args: argparse.Namespace) -> int:
    expected = _titles_from_enex([Path(p).expanduser() for p in args.enex])
    try:
        found = note_titles(args.account, args.folder)
    except RuntimeError as e:
        console.print(f"[red]AppleScript failed:[/] {e}")
        try:
            console.print(f"Folders in {args.account}: {list_folders(args.account)}")
        except RuntimeError:
            pass
        return 2
    result = compare_titles(expected, found)
    color = "green" if result.ok else "red"
    console.print(f"[{color}]expected {result.expected}, found {result.found} in {args.account}/{args.folder}[/]")
    for t in result.missing:
        console.print(f"  [red]missing[/] {t}")
    for t in result.extra:
        console.print(f"  [yellow]extra[/]   {t}")
    return 0 if result.ok else 1


def cmd_show(args: argparse.Namespace) -> int:
    keep_dir = Path(args.keep_dir).expanduser()
    opts = Options(strip_emoji_tags=args.strip_emoji_tags)
    for n in load_keep_dir(keep_dir):
        if args.match.lower() in (n.title + n.source).lower():
            console.rule(note_title(n))
            console.print(f"tags={note_tags(n, opts)} created={n.created.isoformat()} archived={n.archived}")
            console.print(note_body(n, note_resources(n), opts), markup=False, highlight=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="keep2notes", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="Convert a Takeout Keep folder into ENEX batches")
    c.add_argument("keep_dir")
    c.add_argument("-o", "--out", default="out")
    c.add_argument("--batch-size", type=int, default=75)
    c.add_argument("--pilot", type=int, default=0, metavar="N", help="write Pilot.enex with N feature-covering notes")
    c.add_argument("--strip-emoji-tags", action="store_true")
    c.add_argument("--include-trashed", action="store_true")
    c.set_defaults(func=cmd_convert)

    s = sub.add_parser("smoke", help="Write Smoke.enex with synthetic one-feature-each notes")
    s.add_argument("-o", "--out", default="out")
    s.add_argument("--strip-emoji-tags", action="store_true")
    s.set_defaults(func=cmd_smoke)

    v = sub.add_parser("verify", help="Compare an Apple Notes folder against ENEX files (read-only)")
    v.add_argument("enex", nargs="+")
    v.add_argument("--account", default="iCloud")
    v.add_argument("--folder", required=True)
    v.set_defaults(func=cmd_verify)

    sh = sub.add_parser("show", help="Print the converted ENML body for notes matching a title")
    sh.add_argument("keep_dir")
    sh.add_argument("match")
    sh.add_argument("--strip-emoji-tags", action="store_true")
    sh.set_defaults(func=cmd_show)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
