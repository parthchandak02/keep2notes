# keep2notes

Convert a Google Keep export (Google Takeout) into Evernote `.enex` files that Apple Notes imports with:

- native checklists, with checked state
- native tags from Keep labels (several per note)
- original created and modified dates
- embedded images
- bold text, headings (as bold), and links

Archived notes are written to separate files so they can go into their own folder.

## How to use

1. Export Keep from [Google Takeout](https://takeout.google.com) and unzip it. You need the `Takeout/Keep` folder.
2. Install [uv](https://docs.astral.sh/uv/), then from this repo:

   ```bash
   uv run keep2notes smoke -o out                                  # 8 synthetic test notes
   uv run keep2notes convert ~/Downloads/Takeout/Keep -o out --pilot 10   # 10 real notes covering each feature
   uv run keep2notes convert ~/Downloads/Takeout/Keep -o out              # everything, in batches of 75
   ```

3. In Notes on the Mac: **File > Import to Notes**, pick one `.enex` file, click Import. Notes creates an "Imported Notes" folder.
4. Check the import (read-only):

   ```bash
   uv run keep2notes verify out/Active-01.enex --account iCloud --folder "Imported Notes"
   ```

   Add `--deep` to also check created/modified dates, tags, images, and checked/unchecked state for every note. It reads a temporary copy of `NoteStore.sqlite`, so the app running it (Terminal, Cursor, etc.) needs Full Disk Access.

`out/report.txt` lists the label-to-tag mapping, pinned notes, shared notes, and anything worth spot-checking.

## Recommended order

Test before the real import. Importing is not undoable, and importing a file twice creates duplicates.

1. Enable **Notes > Settings > "On My Mac" account** and import `Smoke.enex` and `Pilot.enex` there first, so nothing syncs.
2. Import everything into On My Mac once and run `verify`.
3. Import about 10 notes into iCloud and confirm they look right on your phone.
4. Import the real batches into iCloud one at a time, running `verify` after each.

## Options

- `--strip-emoji-tags`: `Gym 💪` becomes `#Gym` instead of `#Gym-💪`.
- `--batch-size N`: notes per `.enex` file (default 75).
- `--include-trashed`: also convert notes in Keep's trash.
- `keep2notes show <Keep dir> "<title>"`: print the converted body of one note.

## What does not carry over

Apple Notes' importer has no field for these, so they need a manual step or are dropped:

- **Pinned:** tagged `#pinned` so you can find and pin them by hand.
- **Sharing:** shared notes are listed in `report.txt`.
- **Note colors:** dropped.
- **Headings:** imported as bold text.
- **Smart Folders:** these can't be created by script. Use File > New Smart Folder with a tag filter.

## Development

```bash
uv run pytest
```

The tests use synthetic notes only. Real exports, `out/`, and `.enex` files are gitignored.
