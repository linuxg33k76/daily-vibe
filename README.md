# The Daily Vibe

A small desktop journal (Python + PySide6). Plain Markdown files, one per day,
in a folder you own. Open them in any editor, sync them with anything
(Syncthing, git, iCloud…). Since 0.4 it keeps several journals in one
**library** folder, and each journal is a self-contained folder:

```
~/Journal/                          ← library root (Preferences → General)
  Personal/
    .dailyvibe.toml                 ← optional: display name, color, icon, overrides
    2026/09/2026-09-26.md
    assets/2026/09/20260926-155955-997794.webp
  Work/
    .dailyvibe.toml
    2026/09/2026-09-24.md
    assets/2026/09/…webp
```

Image links inside entries are relative (`![x](../../assets/2026/09/x.webp)`),
so you can copy a journal folder anywhere and it still renders.

**Features:** calendar with highlighted days, timeline, full-text search, a
Markdown editor with syntax highlighting and live preview, autosave, "On This
Day" memories, journaling streaks, images pasted or dropped in (converted to
WebP), PDF export, themes (Catppuccin, Gruvbox, Nord, Solarized, Tokyo Night…),
an optional app lock, moving the library to a new folder safely, a plugin
system (built-in: weather with a MET Norway backup, moon phase), **multiple
journals** and **tags** (inline `#tags` and front matter, with a tag panel,
filters, autocomplete, clickable chips and a safe **Rename Tag** with preview
and undo), and a **Journal Settings** dialog per journal.

## Run it

Requires Python 3.11+.

### Linux (Arch / Omarchy)

```bash
sudo pacman -S --needed python noto-fonts-emoji   # Qt comes from the PySide6 wheel
cd journal-app
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'          # or: pip install -r requirements.txt
daily-vibe                       # or: python -m daily_vibe
```

On Wayland (Hyprland), Qt uses Wayland natively if `qt6-wayland` is installed
(`sudo pacman -S qt6-wayland`); otherwise run with `QT_QPA_PLATFORM=xcb`. The app
sets the Wayland app-id `daily-vibe` for window rules.

### macOS

```bash
brew install python@3.12
cd journal-app
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
daily-vibe
```

The app sets its name to "The Daily Vibe", so menu items read "About The Daily
Vibe" and Preferences sits in the app menu (`Cmd+,`). The bold app-menu title and
Dock name come from the app bundle, though: run from a venv they read "Python".
To get the proper name and icon, build a bundle (e.g. `pyinstaller --windowed
--name "The Daily Vibe" -i daily_vibe/resources/icon.svg ...`). Not done yet.

### Tests

```bash
python -m pytest -q
```

`scripts/demo_round8.py` runs the X Bookmarks plugin against an in-process **mock** X API
with clearly fake demo posts (no network, no account) into `demo/r8/` and saves `screenshots/r8_*`.
`scripts/demo_round7.py` builds `demo/r7/` and saves `screenshots/r7_*` (tables,
nested lists, inline images, find & replace, typography, Reading view).
`scripts/demo_round5.py` reuses that library builder under `demo/r5/` and saves
`screenshots/r5_*` (Journal Settings, tag rename preview, undo).
`scripts/demo_round4.py` builds a throwaway demo library under `demo/r4/`
(Personal + Work journals with tags and images, isolated config dir), drives the
app, runs a migration on a copy of old-layout data and saves `screenshots/r4_*`
plus a tree listing and PDFs/zip in `demo/r4/export/`. (`demo_round3.py` is the
previous round's demo.)

## Using it

| Action | How |
|---|---|
| Today's entry (created from template + plugins if missing) | **Today** button / `Ctrl+T` |
| Open a day | calendar, timeline, On This Day list; `Alt+←` / `Alt+→` |
| Create a past/future day from the template | Entry → Create Entry (with template) |
| Switch journal | picker at the top of the sidebar; **+** or `Ctrl+Shift+N` for a new one |
| Journal actions | **Journal** menu: New, Rename, Open Folder, **Journal Settings…** (`Ctrl+Shift+J`, also the ⚙ button / right-click on the picker), Export (zip / PDF), Move or Copy To…, Remove from Library, Delete (to Trash), Show Removed Journals, Rename Tag…, Undo Last Tag Rename, Tag Rename History…, Tag Browser…, Advanced → Edit Journal Settings File / Open Tag-Rename Backups Folder / Migrate Old-Layout Entries |
| Rename a tag | right-click a tag in the Tags panel → Rename #tag…, or Journal / Edit → Rename Tag… |
| Search | search box (`Ctrl+Shift+F`); all words must match; `tag:work` requires a tag; scope **This journal / All journals** next to the box |
| Filter by tag | tick tags in the **Tags** panel (AND), click a tag chip in the preview; **Clear filter** |
| Insert image | paste (`Ctrl+V`), drag a file onto the editor, or `Ctrl+Shift+I` |
| Re-run plugins for this entry | **Refresh plugins** / `Ctrl+R` |
| View mode | toolbar or View → View Mode: **Live Preview** `Ctrl+1` (default), **Source + Preview** `Ctrl+2`, **Source Only** `Ctrl+3`, **Reading** `Ctrl+4`; `Ctrl+P` toggles the preview pane |
| Insert emoji | Edit → Insert Emoji… `Ctrl+.` (or the 😀 toolbar button) |
| Find / replace in this entry | `Ctrl+F` / `Ctrl+H`; `F3` / `Shift+F3` (or `Enter` / `Shift+Enter`) next/previous; `Esc` closes |
| Line up a Markdown table | cursor in the table → Edit → **Format Table** `Ctrl+Alt+T` |
| All entries with a tag | double-click a tag in the Tags panel, right-click / `Ctrl`+click a tag in the editor, click a chip in the preview, or Journal → **Tag Browser…** `Ctrl+Shift+T` |
| Tag rename history / undo any rename | Journal → **Tag Rename History…** |
| Toggle On This Day | `Ctrl+Shift+D` (View menu) |
| Export | File → Export → Current Entry to PDF (`Ctrl+Shift+E`) / Date Range to PDF (optional tag filter) / Whole Journal to PDF / Journal Folder as Zip |
| Lock now | Entry → Lock Now (`Ctrl+L`) (needs a password, see Security) |
| Preferences | Edit → Preferences… / `Ctrl+,` (`Cmd+,` in the app menu on macOS) |
| Theme mode | View → Theme Mode or Preferences → Appearance |

Typing into an empty day creates that day's file (without the template). Edits
autosave after 1 s idle, when you switch days, and when you quit.

### Live Preview (default view)

The editor styles Markdown in place, Obsidian-style, while the file stays plain
Markdown. Nothing is ever inserted into or removed from the text.

* Headings are shown larger and bold (per level). **Bold**, *italic*, ~~strike~~,
  ==highlight==, `inline code` and links are styled. Blockquotes get a bar and a band,
  list bullets are drawn as dots, and task items as checkboxes: click ☐/☑ to
  toggle `[ ]`/`[x]` (one undo step). Horizontal rules are drawn as lines, `#tags`
  as chips, fenced code as a monospace band (with its language label), plugin blocks as a
  subtle band labeled with the plugin, and front matter as a small faded band.
* **Syntax markers** (`#`, `**`, `_`, backticks, `>`, link URLs, `<!-- plugin -->`
  comments, the `---` front matter fences) are hidden on every line except
  the line holding the cursor and any selected lines. There you see and edit
  the raw Markdown (markers in a muted color). Only the lines that enter or leave
  that set are re-highlighted, so cursor moves stay cheap on long entries (2,000-line
  entries load in well under a second).
* **Images**: a line containing only an image link (`![alt](path)`) shows the image
  (scaled to the editor width, max 360 px high; WebP, PNG, JPEG…). Images *inside* a
  line of text (`Shoes ![a](a.webp) and ![b](b.webp)`) are drawn inline, up to 160 px
  high and 420 px wide. Put the cursor on the line to see and edit the links.
* **Tables** (GFM pipe tables, `:---` / `:---:` / `---:` alignment) are drawn as a grid,
  with a bold header on a tinted band, borders, striped rows, alignment respected, and
  column widths fitted to the content (scaled down to the editor width if needed; long
  cells are elided with …). The pipes and the delimiter row are hidden. With the cursor
  anywhere in the table, the whole table shows its raw Markdown in the code font.
  **Format Table** (`Ctrl+Alt+T`) pads the cells so the raw text lines up.
* **Nested lists**: bullets change per level (• ◦ ▪, repeating) and thin guides mark
  each level. 2 spaces, 4 spaces or a tab all nest the same way, and each level is the
  same width on screen. Ordered numbers and nested tasks are styled too.
* **Find & Replace** (`Ctrl+F` / `Ctrl+H`): a bar under the editor with match case,
  whole word and regex options, all matches highlighted, an "n of m" counter, Replace,
  and **Replace All** (one undo step). The line with the current match shows its raw
  Markdown.
* `Ctrl`+click opens a link (web links in the browser, local files with the default
  app). `Ctrl`+click or right-click on a tag offers **Show All Entries Tagged #x** and
  **Rename**.
* Autosave, image paste/drop, tag autocomplete, undo/redo, search and theme changes
  all work as in the source view. Typography lives in Preferences → Appearance →
  **Live Preview typography**: text font and size, heading font, code font, line
  spacing, and max text width (a centered reading column). Changes preview live.

How it works: the editor is still a `QPlainTextEdit` holding the exact file text.
A `QSyntaxHighlighter` (`live_highlighter.py`) applies character formats. Hidden
markers get a 1 pt transparent font with ~0 letter spacing. Image lines get a
transparent font sized so the line is as tall as the picture. The editor then paints
bullets, checkboxes, rules, chips and images over the text in `paintEvent`, and
draws the bands as full-width extra selections. Tables use the same idea. Each
row's raw text is hidden and given a fixed line height, and the editor paints the grid
itself (column widths measured from the cell text for each paint). That is sturdier
than trying to line up proportional text with letter spacing, and it never touches the
text. Nested-list indents are widened with letter spacing on the leading spaces (tabs
use a tab stop of one level). I picked this over a `QTextEdit` with
`QTextImageFormat`s because that needs a second "display" document kept in sync with
the file. With one plain-text document, undo, autosave, paste and search can't drift.

View modes (View → View Mode, toolbar, or Preferences → General): **Live Preview**,
**Source + Preview** (the classic split), **Source Only**, **Reading** (rendered
only). The choice is remembered (`view_mode` in config.toml).

### Journals

Every non-hidden sub-folder of the library root is a journal (except 4-digit
year folders, which are the old layout, and `.migration-backup-*`). The picker
at the top of the sidebar switches journals; the last one opened is remembered
(`last_journal` in config.toml). The calendar, timeline, On This Day, streak,
search (unless the scope is *All journals*), tag panel, plugins and PDF export
all follow the selected journal.

* **New**: creates `<root>/<Name>/` with a `.dailyvibe.toml`. Names can't be empty,
  start with `.`, contain `/ \ : * ? " < > |`, look like a year, or be `assets`.
* **Rename**: renames the folder (and the display name). Refused if a folder with
  the new name exists. Case-only renames work.
* **Remove from Library**: hides the journal from the picker; nothing is deleted
  (`hidden_journals` in config.toml; bring it back from *Show Removed Journals*).
* **Delete**: you must type the folder name; the folder then goes to the
  **system Trash** via [`send2trash`](https://pypi.org/project/Send2Trash/)
  (`pip install 'daily-vibe[trash]'`). If send2trash isn't installed the app
  refuses and tells you where the folder is, so you can delete it yourself. It
  never hard-deletes.
* **Export**: zip of the whole folder, or a PDF of every entry (title page = journal name).
* **Move or Copy To…**: moves/copies just this journal (including its
  `.dailyvibe.toml`) with the same verified, never-overwrite transfer as the
  library move.

### Journal Settings

The **Icon** field has a **Choose Emoji…** button: a searchable picker with the full
Unicode emoji set (≈1,900 emoji, no skin-tone variants). It has category tabs,
**Recent** (persisted as `recent_emoji`), and search by name or CLDR keyword ("coffee"
finds ☕). The row under the field shows your recent emoji first. The same picker
inserts emoji into an entry (Edit → Insert Emoji…, `Ctrl+.`). Emoji newer than
`emoji_max_version` (default 15.1) are hidden, because older fonts draw them as boxes.
The data is bundled (`daily_vibe/resources/emoji.json`, regenerated by
`scripts/build_emoji_data.py` from Unicode's `emoji-test.txt` and the CLDR
annotations).

Journal → **Journal Settings…** (or the ⚙ button next to the picker, or right-click
the picker) edits the journal's `.dailyvibe.toml`. **OK** / **Apply** / **Cancel**
work like Preferences, and changes apply live:

* **General**: display name, with an option to **also rename the folder** (same safe rename
  as Journal → Rename, refused if the name is taken); **color** (color picker, or
  *Default* = theme accent); **icon** (a grid of common emoji or type/paste your own).
  Read-only info: folder path (+ Open Folder), entry count, number and total size of
  images in `assets/`, and the date range of the entries.
* **Template**: *Use a custom template for this journal* + editor, *Reset to global
  template*. Unchecked = the Preferences template.
* **Plugins**: *Use global plugin settings*, or *Customize plugins for this journal*:
  enable/disable each installed plugin (name, version, description) and change its
  schema settings for this journal only (e.g. a Work journal with weather for Seattle).
  Only values that differ from Preferences are stored (`[overrides.plugin_settings.<id>]`).
  Secret settings (API keys) stay global.

Edits go through [tomlkit](https://pypi.org/project/tomlkit/), so your comments, key
order and keys the app doesn't know about are kept. (Without tomlkit, unknown keys
are kept but comments are lost.) The raw file is under Journal → Advanced → Edit
Journal Settings File.

`.dailyvibe.toml` (optional, human-readable, travels with the folder):

```toml
# The Daily Vibe journal settings (optional; safe to edit)
name = "Work"                 # display name (default: folder name)
color = "#89b4fa"             # swatch in the picker
icon = "💼"                   # optional emoji

[overrides]                   # optional; omitted keys use the app settings
template = """# {weekday}, {long_date}

{plugins}

## Tasks

"""
plugins = ["moon_phase"]      # enabled plugins for this journal (order = block order)

[overrides.plugin_settings.weather]   # per-journal plugin settings (only what differs)
location = "Seattle, WA"
```

### Upgrading from 0.3 (migration)

0.3 stored one journal directly in the folder (`~/Journal/2026/09/…md`, images in
`~/Journal/2026/09/assets/`). On first start 0.4 detects that layout and **asks**:
*Migrate now*, *Not now* (asks again next start), or *Don't ask again* (you can
still run it from Journal → Migrate Old-Layout Entries). Migrating:

1. copies all old year folders to `<root>/.migration-backup-<timestamp>/` and
   verifies the copy (SHA-256) before touching anything;
2. moves entries to `<root>/Personal/YYYY/MM/`, images from `YYYY/MM/assets/` to
   `Personal/assets/YYYY/MM/`, and rewrites image links
   (`](assets/x.webp)` / `src="assets/x.webp"` → `../../assets/YYYY/MM/x.webp`);
3. **never overwrites**: identical files already present count as done, different
   ones are skipped and listed, and the old file stays where it was;
4. verifies each written file before deleting its source, removes empty folders,
   leaves loose files in the root alone, and shows a summary.

Running it again is harmless (nothing left to migrate). The backup folder is
yours to delete once you're happy.

### Tags

* **Inline**: `#tag`, `#deep-work`, `#snake_case`, nested `#health/running`.
  A tag starts with a letter, followed by letters, digits, `-`, `_`, and `/` for nesting.
  Not tags: headings (`# Title`, since there's a space), anything after a
  letter/digit/`&`/`#`/`/` (`C#`, `a#b`, `&#123;`, URL fragments), text inside inline code,
  fenced code, URLs/link targets, HTML comments and plugin blocks, and **hex
  colors**: a 3- or 6-char hex token containing a digit (`#1e1e2e`, `#0af`) or a
  single repeated character (`#fff`, `#000`). Letter-only words such as `#cafe`
  or `#bad` are still tags. Tags must start with a letter, so `#2026goals` isn't one.
* **Front matter** at the very top of the file:
  `tags: [travel, family]`, `tags: travel, family`, or a YAML list (`tags:` then `- travel` lines).
* Grouping is case-insensitive (`#Work` = `#work`); the most common spelling is shown.
  Filtering by `health` also matches `health/running`.
* **Tag panel** (collapsible, under the search box): tags with entry counts for
  the current journal, or all journals when the scope is *All journals*. Tick
  tags to filter the timeline (entries must have **all** ticked tags). **Clear
  filter** resets it. Ticked tags are listed first.
* **Editor**: type `#` and a tag autocomplete pops up (tags from all journals; Enter/Tab
  accepts, Esc closes). Tags are colored (theme key `hl_tag`, default = accent).
* **Preview**: tags render as chips; front matter is hidden and its tags become a
  chip row at the top. Clicking a chip adds it to the filter and offers **Show All
  Entries Tagged #x** / Rename. Normal links open in your browser.
* **Tag entries view** (double-click a tag in the panel, the editor/preview tag menus,
  or Journal → **Tag Browser…** `Ctrl+Shift+T`): replaces the editor area (← **Back to
  entry**) with a card per entry carrying the tag, newest first. Each card shows the date,
  the journal (in *All journals* scope), the title or first line, an excerpt with the tag
  highlighted, and the entry's other tags as chips. Controls: tag picker (type or choose),
  *Include nested tags*, *This journal / All journals*, *Newest / Oldest first*,
  **Rename Tag…** and **Export These to PDF…** (one entry per page, journal name in the
  header when several journals are involved). Click a title to open the entry
  (switching journal if needed). Click a chip to show that tag.
* **Search**: `tag:work standup` = entries tagged work containing "standup".
* **PDF**: Date Range export has an optional "Only tagged" field (AND). Tags print
  as subtle chips, and front matter isn't printed.
* **Index**: tags are cached per file keyed by (mtime, size), like the streak cache,
  so only changed files are re-read; saving refreshes the panel.

### Renaming tags

Right-click a tag in the Tags panel → **Rename #tag…**, or Journal / Edit → **Rename
Tag…**. Choose the new tag (checked with the same rules as the parser), the scope
(*This journal* or *All journals*), and **Include nested tags** (on by default:
`#health` → `#fitness` also turns `#health/running` into `#fitness/running`).
Matching ignores case, like the tag panel; the new tag is written as you typed it.

* **Preview** first: every affected entry with its number of changes and a before → after
  sample line. If the new tag already exists you get a **merge** warning.
  Duplicates in front matter lists are removed (`tags: [health, fitness]` →
  `tags: [fitness]`). Repeated tags in the body are prose and stay.
* Only **real tags** are rewritten: inline tags per the tag rules (not in code spans,
  fenced code, URLs, HTML comments or plugin blocks, not hex colors) and front
  matter tags in all three list forms. `#run` never touches `#running` or `#run-club`;
  `#run/5k` changes only with the nested option. Tags inside heading *text*
  (`# Day #run`) are real tags and are renamed; the heading marker itself never is.
* Files are read and written as bytes, so everything else (line endings incl. CRLF,
  spacing, quotes) stays byte-for-byte. The open entry is saved first. Each file is
  written atomically (temp file + rename), and a file that changed after the preview
  is skipped and reported. The open entry reloads in the editor at about the same
  cursor position, and the tag panel, timeline and active filter (old tag → new tag) update.
* **Undo**: the original files are copied first to
  `<journal>/.dailyvibe/backups/tag-rename-<timestamp>/` with a `manifest.json`.
  Journal → **Undo Last Tag Rename (#old → #new)** restores them, but only files
  that still have exactly the content the rename wrote. Files you edited since are
  left alone and listed (their backup copy stays in the folder).
* **History**: Journal → **Tag Rename History…** lists every rename that still has a
  backup: date, old → new, scope (+ nested), journals, files changed and status
  (*applied*, *undone*, *partially undone* = some files had been edited and were left
  alone). **Undo Selected Rename** works for any applied rename, with one safety rule:
  if a **newer rename that is still applied touched any of the same entries**, you must
  undo that one first (the dialog names it and disables Undo). Undoing newest-first
  always restores cleanly. Skipping conflicting files instead could silently mix two
  renames in one file. The per-file SHA-256 check still protects entries you edited.
* **Retention**: Preferences → General → *Keep tag-rename backups*: the last **N
  renames** (default 20, 0 = all) and/or at most **N days** (default: no age limit).
  Older backups are deleted after each rename, at startup and when you apply
  Preferences (`tag_rename_keep`, `tag_rename_keep_days`).
* Journal zip exports **leave out** `.dailyvibe/backups` unless you tick *Include
  tag-rename backups* in the export prompt.

### On This Day

The panel under the timeline lists entries from the same month/day in earlier
years (with the year and a short excerpt), plus **one month ago** and **one week
ago** when those days have entries. Click one to open it. It follows the
selected date.

Feb 29: when viewing Feb 29, leap years show their Feb 29 and other years fall
back to Feb 28. When viewing Feb 28 in a non-leap year, Feb 29 entries from leap
years are included too.

### Streaks

Under the calendar: `🔥 5-day streak — keep it going!` (or `… write today to make
it 6.` if today isn't written yet), plus your longest streak. A day counts only
when the entry has something you actually wrote. The untouched template, plugin
blocks and placeholders don't count. The streak may end today or yesterday: it
isn't broken until today is over. It updates on every save. You can turn it off
in Preferences → General.

### PDF export

File → Export → **Current Entry to PDF** or **Date Range to PDF** (choose from/to,
one entry per page or continuous, Letter or A4). Uses QTextDocument + QPdfWriter
(no WebEngine):

* print-friendly light styling regardless of the app theme
* plugin marker comments stripped (their content is kept)
* images embedded at full resolution. WebP works through Qt's image plugin; any
  format Qt can't read is converted to PNG first.
* page numbers at the bottom of each page

### Moving your library

When you change the library folder (Preferences → General or File → Change
Library Folder) you're asked to **Move entries**, **Copy entries**, **Just
switch** (use the new folder as it is) or **Cancel**. The open entry is saved
first. Move/copy then runs in the background with a progress dialog:

* what's transferred: every journal folder (entries, `assets/`, `.dailyvibe.toml`)
  and any not-yet-migrated year folders, with the structure preserved. Loose files
  and hidden items (e.g. `.migration-backup-*`) in the old root are left there and reported.
* **never overwrites.** If the target already has a file at the same path:
  * identical content: counted as "already present" (on move the source copy is removed)
  * different content: **skipped and reported**, and the source is kept.

  The app skips rather than renames because a renamed file such as
  `2026-09-26 (1).md` would no longer be recognized as that day's entry.
* each copy is verified (size + SHA-256) before a move deletes the source
* empty folders are cleaned up; the old folder is kept if anything remains
* refused if one folder is inside the other (you can still "just switch")
* a summary is shown at the end

### App lock (optional, off by default)

Preferences → **Security**: set, change or remove a password (with a confirm
field; changing or removing requires the current password). Only a salted
**scrypt** hash is stored in config.toml (`[security] password_hash`); the
password itself is never saved. When a password is set:

* the app starts on a lock screen. No entry content is loaded until you unlock.
* **Lock Now** (`Ctrl+L`) and optional **auto-lock after N idle minutes** (0 = never)
* while locked, the editor, preview, timeline, On This Day, search and streak are
  cleared and hidden, and the menus are disabled. Plugin results that arrive while
  locked are written straight to their entry files and never shown.
* failed attempts back off: after 3 wrong passwords each try waits 2, 4, 8… s (max 60 s)

> **This is an app lock, not encryption.** Your entries stay plain Markdown files
> on disk, readable by anyone with access to your account or drive. There is **no
> password recovery**: if you forget it, quit the app, open config.toml and delete
> the `password_hash` value (or the whole `[security]` table).

## Preferences

Edit → Preferences (`Ctrl+,`). **Apply** applies live and keeps the dialog open,
**OK** applies and closes, **Cancel** discards unapplied changes. Password
changes apply immediately.

- **General**: library folder (Browse…, move/copy prompt), autosave interval, show
  streak, show On This Day, editor view mode, tag-rename backup retention, new-entry template.
- **Appearance**: theme mode System/Light/Dark, light and dark theme presets,
  accent color, editor (Source-mode) font and size, preview font size, and
  **Live Preview typography** (text font and size, heading font, code font, line
  spacing, max text width) with a live sample.
- **Images**: WebP quality and max dimension.
- **Plugins**: every plugin found (name, version, source, status and errors);
  enable/disable; auto-generated settings form per plugin; user plugins folder
  with Open/Reload buttons.
- **Security**: app lock password and auto-lock.

The raw file is still available under File → Advanced → Edit Config File.

## Configuration

`config.toml` in the platform config dir:

- Linux: `~/.config/daily-vibe/config.toml`
- macOS: `~/Library/Application Support/daily-vibe/config.toml`
- Override the directory with `DAILY_VIBE_CONFIG_DIR=/some/dir` (the old
  `JOURNAL_APP_CONFIG_DIR` still works as a fallback).
- **Migration:** on first run, an existing `journal-app` config dir (from before
  the rename) is copied to `daily-vibe`. The old one is left in place.

```toml
journal_root = "~/Journal"   # the library root: one sub-folder per journal
last_journal = "Personal"    # remembered automatically
hidden_journals = []         # "Remove from Library" (folders stay on disk)
search_scope = "journal"     # or "all"
migration_declined = ""      # set by "Don't ask again" on the migration prompt
autosave_ms = 1000
show_preview = true
show_on_this_day = true
show_streak = true
template = """# {weekday}, {long_date}

{plugins}

## Notes

"""

[security]
password_hash = ""        # salted scrypt hash; delete to remove the app lock
auto_lock_minutes = 0     # 0 = never

[appearance]
mode = "system"           # "light" | "dark" | "system"
light_theme = "Default Light"
dark_theme = "Catppuccin Mocha"
accent = ""               # "#rrggbb" to override the theme accent
editor_font_family = ""   # "" = system monospace
editor_font_size = 12
preview_font_size = 14
live_font_family = ""      # Live Preview text font ("" = system UI font)
live_font_size = 0         # pt; 0 = editor size + 1
live_line_spacing = 1.0    # 1.0 – 2.5
live_heading_family = ""   # "" = same as the text font
live_code_family = ""      # "" = the editor font
live_max_width = 0         # px; > 0 centers a reading column

[images]
webp_quality = 80
max_dimension = 1920

[plugins]
enabled = ["weather", "moon_phase"]   # order = order of blocks

[plugins.weather]
location = ""             # "" = auto from IP; "Denver, CO"; or "39.74,-104.99"
units = "imperial"        # or "metric"
contact = ""              # optional email/URL appended to the MET Norway User-Agent

[plugins.moon_phase]
show_details = true
hemisphere = "northern"
```

Template placeholders: `{date}` (2026-09-26), `{weekday}`, `{long_date}`,
`{plugins}` (where plugin blocks go; if it's absent, they go after the first `# ` heading).

## Plugins

See **[docs/PLUGINS.md](docs/PLUGINS.md)** for the full API. In short, a plugin is
a Python file (or an installed package exposing the `daily_vibe.plugins` entry
point) with `ID`, `NAME`, `VERSION`, `DESCRIPTION`, `AUTHOR`, `API_VERSION`, an
optional `SETTINGS` schema that Preferences renders automatically, a required
`render(date, context)`, and optional `on_load` / `on_entry_created` hooks.
[`examples/plugin_template.py`](examples/plugin_template.py) is a working offline
"Quote of the Day" to copy.

Plugins run on a thread pool, one job per plugin, so a slow plugin never holds up
the others. A new entry is written right away with `⏳ Fetching…` placeholders.
Each result is applied to the entry it was requested for: in the editor if that
entry is open (only its marker block changes), otherwise in the file on disk. If
the same plugin is refreshed again for the same day before the first run
finishes, the older result is discarded. A failing plugin shows a `⚠️` note in
its block.

### Built-ins

- **weather**: location from IP (ipapi.co, then ip-api.com, then ipwho.is) or a fixed
  place (the **Look up** button geocodes it with Open-Meteo). Today shows current
  conditions + high/low, precipitation and max wind (Open-Meteo forecast, falling
  back to its historical-forecast endpoint). Past dates use the Open-Meteo archive;
  future dates up to 16 days get a forecast. Results are cached for 15 min to spare
  the free API quota. **Backup provider:** if Open-Meteo fails (HTTP 429 daily
  quota, outage, timeout) for today or a date up to 9 days ahead, the key-free
  [MET Norway Locationforecast 2.0](https://api.met.no/weatherapi/locationforecast/2.0/documentation)
  API is used. The block then says `· MET Norway` plus a "backup" line. MET's terms
  require an identifying User-Agent: the app sends
  `DailyVibe/0.4 (The Daily Vibe desktop journal app)` and appends the optional
  `contact` setting if you fill it in. MET has no history, so past dates still need
  Open-Meteo. MET gives UTC times without a time zone; the day is cut at local
  midnight using the location's zone when known (else lon/15 h). For today the
  high/low/precipitation cover the remaining hours, and "feels like" isn't available.
- **moon_phase**: offline calculation; phase, illumination %, emoji (mirrored for
  the southern hemisphere).
- **x_bookmarks** (0.8): posts you newly bookmarked on X, added to today's entry.
  Official X API v2 with OAuth 2.0 PKCE and your own developer app. X API usage is
  **paid**. See [X Bookmarks](#x-bookmarks) below.
- **ai_highlights**: documented stub, not implemented.

### X Bookmarks

Adds the posts you bookmarked on X since the last check to **today's** entry:

```markdown
**Ada Example** ([@ada_example](https://x.com/ada_example)) · 2026-09-25 · [View post](https://x.com/ada_example/status/1…)

> Post text, with t.co links expanded to the real URL (https://example.com/article)
>
> ↪ Quoting Bob Sample (@bob_sample): quoted text · [link](https://x.com/bob_sample/status/…)

![Alt text from the post](../../assets/2026/09/x-1…-1.webp)
```

* **How "new" is decided.** X's bookmarks endpoint doesn't say *when* you bookmarked
  something. So the plugin remembers every post ID it has seen, per journal, in
  `<journal>/.dailyvibe/x_bookmarks.json`, and treats unseen IDs as today's. Posts
  are stored there with the date they were first seen. Refreshing today's block
  never loses or duplicates posts, and past days re-render from that file with no
  network access. Only today's entry calls the API.
* **First sync** (per journal): *Ask first* (default) fetches nothing and puts a
  note in the block. *Import none* reads one page and marks those bookmarks as seen.
  *Import last N* adds your N most recent bookmarks to today and marks the rest of
  that page as seen. You won't get hundreds of old bookmarks dumped into one day.
* **Paging and cost.** Each sync reads pages of *Posts per request* (default 20),
  newest bookmark first. It stops at the first page that contains a post it has
  already seen, or after *Max pages* (default 3). If it stops early, the next sync
  looks further back. X bills per post returned, so small pages keep the cost down.
* **Rate limits.** `GET /2/users/:id/bookmarks` allows 180 requests per 15 min per
  user. On HTTP 429 the plugin waits if `x-rate-limit-reset` is ≤ 30 s away,
  otherwise it stops and says when to retry. Posts found before the limit are kept.
* **Media.** With *Save images* on, photos (and video/GIF preview frames) are saved
  as WebP in `<journal>/assets/YYYY/MM/x-<post id>-<n>.webp` and linked relatively.
  With it off, or if a download fails, the block links to the image on X instead.
* **Tokens.** The access and refresh tokens (auto-refreshed with `offline.access`;
  X rotates refresh tokens) go in the OS keyring (`pip install 'daily-vibe[secrets]'`).
  If no keyring backend works, they go in `<config dir>/x_bookmarks_tokens.json`
  with 0600 permissions and a `_warning` field, and Preferences says so.
  **Disconnect** revokes the tokens at X (best effort) and deletes them locally.
* **Failures** are shown in the block (not connected, credits used up, rate limited…).
  If today already has stored posts, they stay and a warning line is added below them.

#### X API access and cost (checked 2026-09-26)

* Since **Feb 6, 2026**, X's self-serve API is **pay-per-use**: you buy credits in
  the Developer Console ([console.x.com](https://console.x.com)), with no
  subscription. The pricing page lists no free tier. At launch, recently active
  Legacy Free users got a one-time $10 voucher, and Basic/Pro plans "remain
  available" to existing subscribers. Whether new sign-ups can still pick
  Basic/Pro isn't clear from the docs.
  ([X API changelog](https://docs.x.com/changelog))
* `GET /2/users/{id}/bookmarks` is an **"Owned Read"**: **$0.001 per resource**
  (1,000 posts for $1), effective **Apr 20, 2026**. This rate applies when `{id}` is the
  authenticated user *and* that user owns the developer app, which is the setup
  described here. Otherwise the standard Posts: Read rate of $0.005 per post applies.
  ([Pricing](https://docs.x.com/x-api/getting-started/pricing))
* Resources are deduplicated per 24-hour UTC day, so refreshing the same day again
  doesn't pay twice for the same posts.
* Rough cost with the defaults: one page of 20 posts per day is about $0.02/day, or
  roughly $0.60/month. The docs don't say whether the expanded author/media objects
  in a response are billed separately (User: Read is listed at $0.010 per resource).
  Check the usage page in the console after your first syncs.
* Prices change. The Developer Console always has the current rates.

#### How to get an X developer app Client ID

1. Sign in at **[console.x.com](https://console.x.com)** with your X account, accept the
   Developer Agreement and fill in the short profile.
2. Buy a small amount of credits (and consider setting a **spending limit**). Without
   credits the API refuses requests and the block shows the error.
3. Create a **New App** (name, description, use case such as "personal journal that
   saves my own bookmarks").
4. In the app's **User authentication settings**, enable **OAuth 2.0** and pick
   **Native App** as the type. That's a public client: PKCE, no client secret.
   Permissions: **Read** is enough.
5. Add the **Callback URI** `http://127.0.0.1:8765/callback`. It must match exactly.
   X wants `127.0.0.1`, not `localhost`. If you change *Redirect port* in the
   plugin, change it here too. (The console may also ask for a website URL.)
6. Copy the **OAuth 2.0 Client ID** from **Keys and tokens**. Don't use the API Key or
   Bearer Token.
7. In The Daily Vibe: Preferences → Plugins → **X Bookmarks**. Tick it, paste the
   Client ID, click **Apply**, then **Connect X account**. Approve in the browser,
   choose **First sync**, and run **Entry → Refresh Plugin Blocks**.

If you chose a confidential app type (Web App / Automated App), also paste its
Client Secret. It's sent with HTTP Basic auth and stored like other secrets.

Scraping x.com is against X's terms and isn't an option. The plugin only uses the
official API with your own app and credentials.

## Themes

Built-in presets: Default Light, Default Dark, Nord, Solarized Light, Gruvbox Dark,
Catppuccin Mocha, Catppuccin Latte, Tokyo Night. A theme is one TOML file in
`daily_vibe/themes/` or `<config dir>/themes/` (File → Advanced → Open User Themes
Folder, then Reload Themes):

```toml
name = "My Theme"
mode = "dark"            # or "light"
[colors]
window = "#1e1e2e"       # window chrome
surface = "#181825"      # editor / preview / list background
alt_surface = "#313244"
text = "#cdd6f4"
muted = "#a6adc8"
button = "#313244"
border = "#45475a"
accent = "#cba6f7"
link = "#89b4fa"
heading = "#f5c2e7"
code_bg = "#313244"
quote_bg = "#26263a"
quote_text = "#f9e2af"
weekend = "#f38ba8"
# optional editor syntax colors (fallbacks derived from the above):
hl_heading = "#f5c2e7"
hl_emphasis = "#f2cdcd"
hl_code = "#a6e3a1"
hl_link = "#89b4fa"
hl_image = "#94e2d5"
hl_list = "#fab387"
hl_quote = "#f9e2af"
hl_marker = "#6c7086"    # plugin <!-- markers --> (dimmed)
hl_tag = "#cba6f7"       # #tags in the editor and chip background in the preview (default: accent)
```

The editor highlights headings, bold, italic, inline code, fenced code blocks,
links, images, lists, blockquotes and plugin markers, and recolors live when the
theme changes. Qt's Fusion style is used everywhere so the palette applies
consistently. "System" mode follows `QStyleHints.colorScheme()` (Qt 6.5+) when
the platform reports it; many tiling-WM setups report nothing, so pick Dark
explicitly there.

## Layout

```
daily_vibe/
  app.py               entry point (daily-vibe / python -m daily_vibe)
  config.py            TOML config, paths, env vars, legacy migration
  storage.py           Journal: paths, read/write, list, search, templates, overrides
  library.py           Library: journals in the root, create/rename/trash/zip, cross-journal search
  journal_meta.py      per-journal .dailyvibe.toml
  migrate.py           0.3 single-journal layout → "Personal" journal
  tags.py              tag rules, front matter, TagIndex cache, chip rendering
  tag_rename.py        parser-based tag rename, atomic writes, backups, history, undo, retention
  tag_view.py          data + HTML cards for the tag entries view
  emoji_data.py        bundled emoji data, search, recent list
  live_highlighter.py  Live Preview formats (marker hiding, headings, lists, tables, overlays data)
  md_tables.py         GFM table parsing, cell text, Format Table
  md_ext.py            Markdown extensions for preview/PDF: strikethrough, tables, list nesting
  find_replace.py      find / replace logic (case, whole word, regex)
  markers.py           plugin block markers
  images.py            Pillow → WebP + relative links
  render.py            Markdown → HTML (preview)
  export.py            PDF export
  highlighter.py       editor syntax highlighting
  on_this_day.py       memories from earlier years / month / week
  streak.py            streak math
  security.py          scrypt hashing + attempt back-off
  relocate.py          safe move/copy of the library or a single journal
  secrets.py           keyring-or-config secret storage
  plugin_manager.py    plugin API v1: discovery, schema, hooks, running
  workers.py           QThreadPool runners
  theming.py, themes/  theme registry + presets
  plugins/             built-in plugins
  resources/           icon.svg, emoji.json
  ui/                  main window, preferences, plugin page, panels, lock, dialogs,
                       journal_settings.py, tag_rename_dialog.py, tag_history_dialog.py,
                       tag_entries.py (tag entries view), emoji_picker.py, editor.py (Live Preview),
                       find_bar.py (Find & Replace bar)
docs/PLUGINS.md        plugin developer guide
examples/plugin_template.py
tests/                 pytest (Qt tests run offscreen)
scripts/demo_round7.py tables / lists / inline images / find / typography screenshots (demo_round4-6.py: earlier)
scripts/build_emoji_data.py  regenerates resources/emoji.json
```
# daily-vibe
