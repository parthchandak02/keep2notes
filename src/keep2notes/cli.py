"""Convert Google Keep Takeout exports into ENEX files for Apple Notes."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .cleanup import load_overlays, note_payload, render_blocks, validate_overlay, write_chunks
from .enex import Options, note_body, note_resources, note_tags, note_title, write_enex
from .html_clean import enml_to_text, html_to_enml, normalize_ws
from .model import KeepNote, load_keep_dir
from .smoke import smoke_notes
from .tags import label_to_tag
from .notestore import read_folder
from .review import write_review
from .verify import compare_titles, deep_compare, expected_from_enex, list_folders, note_titles

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
    empty = [n for n in notes if n.is_empty]
    if not args.keep_empty:
        notes = [n for n in notes if not n.is_empty]
    if args.overlays:
        overlays = load_overlays(Path(args.overlays).expanduser())
        bad = []
        for n in notes:
            ov = overlays.get(n.source)
            if ov is None:
                continue
            rep = validate_overlay(n, ov)
            if rep.ok:
                n.overlay = ov
            else:
                bad.append(n.source)
        console.print(f"overlays applied: {sum(n.overlay is not None for n in notes)}, rejected: {len(bad)}")
        for s in bad:
            console.print(f"  [red]rejected[/] {s} (run cleanup-validate for details)")

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
    if empty and not args.keep_empty:
        console.print(f"[yellow]Dropped {len(empty)} empty notes:[/] " + ", ".join(n.source for n in empty))
    for f in files:
        console.print(f"  {f}")
    console.print(f"[green]Report:[/] {out / 'report.txt'}  ({len(mismatches)} rich-text diffs to spot-check)")
    return 0


def pick_cleanup_pilot(notes: list[KeepNote], limit: int) -> list[KeepNote]:
    def dash_lines(n: KeepNote) -> int:
        return sum(1 for line in n.text.splitlines() if line.lstrip().startswith("- "))

    checks = [
        lambda n: dash_lines(n) >= 5,
        lambda n: any(line.strip().startswith("#") for line in n.text.splitlines()[:3]),
        lambda n: len(n.items) > 25 and any(i.checked for i in n.items),
        lambda n: 3 <= len(n.items) <= 12,
        lambda n: "font-weight:700" in n.html,
        lambda n: "\n" in n.title,
        lambda n: bool(n.attachments) and len(n.text) > 20,
        lambda n: bool(n.annotations) and len(n.text) > 40,
        lambda n: n.archived and n.created.year < 2016 and len(n.text) > 80,
        lambda n: 150 < len(n.text) < 600 and dash_lines(n) == 0,
        lambda n: 1500 < len(n.text) < 4000,
        lambda n: not n.title and len(n.text) > 30,
    ]
    picked: list[KeepNote] = []
    for check in checks:
        if len(picked) >= limit:
            break
        match = next((n for n in notes if n not in picked and not n.is_empty and check(n)), None)
        if match:
            picked.append(match)
    return picked


def cmd_cleanup_prep(args: argparse.Namespace) -> int:
    notes = [n for n in load_keep_dir(Path(args.keep_dir).expanduser()) if not n.trashed and not n.is_empty]
    out = Path(args.out).expanduser()
    if args.skip_done:
        done = set(load_overlays(Path(args.skip_done).expanduser()))
        notes = [n for n in notes if n.source not in done]
    if args.pilot:
        pilot = pick_cleanup_pilot(notes, args.pilot)
        (out / "chunks").mkdir(parents=True, exist_ok=True)
        path = out / "chunks" / "pilot.json"
        path.write_text(json.dumps([note_payload(n) for n in pilot], ensure_ascii=False, indent=1), encoding="utf-8")
        console.print(f"[bold]Pilot chunk[/] {len(pilot)} notes -> {path}")
        for n in pilot:
            console.print(f"  - {n.source}")
        return 0
    paths = write_chunks(notes, out / "chunks", args.chunks)
    console.print(f"{len(notes)} notes -> {len(paths)} chunks in {out / 'chunks'}")
    for p in paths:
        console.print(f"  {p.name}: {p.stat().st_size // 1024} KB")
    return 0


def cmd_cleanup_validate(args: argparse.Namespace) -> int:
    notes = {n.source: n for n in load_keep_dir(Path(args.keep_dir).expanduser())}
    overlays = load_overlays(Path(args.overlays).expanduser())
    pairs = []
    for source, ov in overlays.items():
        note = notes.get(source)
        if note is None:
            console.print(f"[red]{source}: no matching Keep note[/]")
            continue
        rep = validate_overlay(note, ov)
        if rep.ok:
            note.overlay = ov
            try:
                note_body(note, [], Options())
                ET.fromstring(f"<en-note>{render_blocks(ov['blocks'])}</en-note>")
            except ET.ParseError as e:
                rep.errors.append(f"invalid markup: {e}")
            finally:
                note.overlay = None
        pairs.append((note, ov, rep))
        status = "[green]ok[/]" if rep.ok else "[red]REJECT[/]"
        console.print(f"{status} {source}  fixes={len(rep.typo_fixes)} added={len(rep.added)}", highlight=False)
        for e in rep.errors[:8]:
            console.print(f"    [red]{e}[/]", highlight=False)
    bad = sum(not r.ok for _, _, r in pairs)
    console.print(f"{len(pairs)} overlays, {len(pairs) - bad} ok, {bad} rejected")
    if args.review:
        path = write_review(Path(args.review).expanduser(), pairs)
        console.print(f"review page: {path}")
    return 0 if not bad else 1


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


def cmd_verify_deep(args: argparse.Namespace) -> int:
    expected = expected_from_enex([Path(p).expanduser() for p in args.enex])
    try:
        stored = read_folder(args.account, args.folder)
    except (PermissionError, LookupError, FileNotFoundError) as e:
        console.print(f"[red]{e}[/]")
        return 2
    problems = deep_compare(expected, stored)
    t = Table(title=f"deep verify: {args.account}/{args.folder}")
    t.add_column("check")
    t.add_column("expected", justify="right")
    t.add_column("in Notes", justify="right")
    t.add_row("notes", str(len(expected)), str(len(stored)))
    t.add_row("checked items", str(sum(e.checked for e in expected)), str(sum(s.checked for s in stored)))
    t.add_row("unchecked items", str(sum(e.unchecked for e in expected)), str(sum(s.unchecked for s in stored)))
    t.add_row("tags", str(sum(len(e.tags) for e in expected)), str(sum(len(s.tags) for s in stored)))
    t.add_row("images", str(sum(e.resources for e in expected)), str(sum(s.attachments for s in stored)))
    console.print(t)
    for title, diffs in problems:
        console.print(f"[red]{title}[/]: " + "; ".join(diffs), markup=True, highlight=False)
    console.print("[green]all notes match[/]" if not problems else f"[red]{len(problems)} notes differ[/]")
    return 0 if not problems else 1


def cmd_verify(args: argparse.Namespace) -> int:
    if args.deep:
        return cmd_verify_deep(args)
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
    c.add_argument("--keep-empty", action="store_true", help="keep notes with no title, text, items, or images")
    c.add_argument("--overlays", metavar="DIR", help="apply validated cleaned-note overlays from DIR")
    c.set_defaults(func=cmd_convert)

    cp = sub.add_parser("cleanup-prep", help="Write note chunks for editors (humans or agents) to clean")
    cp.add_argument("keep_dir")
    cp.add_argument("-o", "--out", default="out/cleanup")
    cp.add_argument("--chunks", type=int, default=8)
    cp.add_argument("--pilot", type=int, default=0, metavar="N", help="write a single pilot chunk of N varied notes")
    cp.add_argument("--skip-done", metavar="DIR", help="exclude notes that already have an overlay in DIR")
    cp.set_defaults(func=cmd_cleanup_prep)

    cv = sub.add_parser("cleanup-validate", help="Check overlays against the originals")
    cv.add_argument("keep_dir")
    cv.add_argument("overlays")
    cv.add_argument("--review", metavar="HTML", help="also write a before/after review page")
    cv.set_defaults(func=cmd_cleanup_validate)

    s = sub.add_parser("smoke", help="Write Smoke.enex with synthetic one-feature-each notes")
    s.add_argument("-o", "--out", default="out")
    s.add_argument("--strip-emoji-tags", action="store_true")
    s.set_defaults(func=cmd_smoke)

    v = sub.add_parser("verify", help="Compare an Apple Notes folder against ENEX files (read-only)")
    v.add_argument("enex", nargs="+")
    v.add_argument("--account", default="iCloud")
    v.add_argument("--folder", required=True)
    v.add_argument(
        "--deep",
        action="store_true",
        help="read NoteStore.sqlite (needs Full Disk Access) to check dates, tags, images, checklist state",
    )
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
