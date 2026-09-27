# Changelog

## 0.8.1
- **US English throughout**: all UI text (labels, dialog titles, tooltips, status and
  error messages), the README, docs, theme files, code comments/docstrings and test
  names now use US spelling (e.g. Journal Settings shows **Color:**, Preferences shows
  **Accent color:**, "Transfer canceled", "Sign-in canceled", "centered", "recognized").
- **Backward compatibility**: a `.dailyvibe.toml` that uses the British-spelled color
  key (`colour = "#…"`) is still read as a fallback when `color` is absent, and is <!-- legacy-spelling -->
  renamed to `color` the next time the app saves that journal's settings (comments and
  other keys preserved). `relocate.transfer(canceled=…)` / `TransferReport.canceled`
  replace the old British-spelled names, which remain as deprecated aliases.
- New regression test (`tests/test_us_english.py`) fails if common British spellings
  reappear in `daily_vibe/**/*.py`, bundled themes, the README, `docs/` or `examples/`,
  or in test function names.

## 0.8.0
- **X Bookmarks plugin (real implementation, replaces the stub)**: posts you newly
  bookmarked on X are added to today's entry. It uses the official X API v2
  (`GET /2/users/:id/bookmarks`) and your own developer app.
  - OAuth 2.0 Authorization Code + **PKCE** (S256). You paste your app's Client ID;
    **Connect X account** opens the system browser. A one-shot loopback server on
    `http://127.0.0.1:<port>/callback` receives the code, and `state` is verified.
    Scopes: `tweet.read users.read bookmark.read offline.access`. An optional Client
    Secret is supported for confidential app types. **Disconnect** revokes the
    tokens (best effort) and deletes them.
  - Tokens are stored in the OS keyring, or in a clearly labeled 0600 fallback file
    when no keyring backend works. Access tokens are refreshed automatically before
    they expire and once more on a 401. Rotated refresh tokens are saved straight
    away, and a lock ensures only one refresh runs at a time.
  - Seen post IDs are tracked per journal in `<journal>/.dailyvibe/x_bookmarks.json`.
    New posts are stored under the day they were first seen, so refreshes are stable
    and past days render offline. **First sync** choice: Ask first (default, fetches
    nothing) / Import none / Import last N.
  - Pagination stops at the first already-seen post (with *Max pages* as a cap, and a
    catch-up on the next sync if it stopped early). HTTP 429 handling uses
    `x-rate-limit-reset` / `retry-after`: short waits are retried, long ones fail
    with the reset time. Clear messages for "not connected", credits used up, and
    other errors. A failure keeps today's already-stored posts.
  - Output per post: author name and @handle, text with t.co links expanded
    (`expanded_url`; long posts via `note_tweet`), post link, created date, an
    optional quoted-post line, and optional images saved as WebP in
    `assets/YYYY/MM/`. The text is escaped so it can't become headings, lists or
    HTML, and can't break the plugin markers.
  - New package `daily_vibe/xapi/` (`net`, `oauth`, `tokens`, `client`, `sync`,
    `render`). There are no new required dependencies: it uses urllib, and `keyring`
    stays optional.
- **Plugin settings: new `action` field type**: button rows with a status line, whose
  callbacks run in the background with the form's unsaved values (docs/PLUGINS.md).
  Plugin forms now scroll, and help text sits directly under its field (no more
  over-tall rows for wrapped hints).
- README: X API pricing notes (checked 2026-09-26, with sources) and step-by-step
  Client ID setup.
- `scripts/demo_round8.py`: a mocked X API with fake demo data, saving `screenshots/r8_*`.

## 0.7.0
- **Live Preview tables** (GFM pipe tables with `:---` / `:---:` / `---:` alignment):
  outside the table they're drawn as a grid, with a bold header on a tinted band, cell
  borders, alternating row shading, alignment respected, and column widths measured from
  the content. The pipes and the delimiter row are hidden. Cells that are entirely
  bold, italic, strikethrough or code keep that style. Put the cursor anywhere in the
  table and the whole table switches to raw monospace Markdown. **Format Table**
  (Edit menu, `Ctrl+Alt+T`) pads the cells so the raw Markdown lines up (one undo step,
  wide emoji/CJK aware).
- **Nested lists**: the bullet changes per level (• ◦ ▪, then repeats), ordered numbers
  are styled, and thin indent guides mark each level. 2-space, 4-space and tab indents
  all nest the same way, and one level has the same width whichever you use. Nested
  task items work too.
- **Inline images inside text lines** (`text ![a](x.webp) more ![b](y.webp)`): images
  are drawn in the line (max 160 px high / 420 px wide) when the cursor isn't on it.
  Lines that contain only an image still show it large, as before.
- **Find & Replace in the current entry**: a slim bar under the editor. Keys: `Ctrl+F`
  find, `Ctrl+H` replace, `F3` / `Shift+F3` or `Enter` / `Shift+Enter` for next/previous,
  `Esc` to close. Options for match case, whole word and regex. Every match is
  highlighted, with an "n of m" counter. Replace, and Replace All as a single undo
  step; regex replacements accept `\1` / `\g<name>`. Works in Live Preview (the line
  with the current match shows its raw Markdown) and in Source mode. Opening it from
  Reading switches to Live Preview. The global "Find in Entries" (`Ctrl+Shift+F`) is
  unchanged.
- **Live Preview typography** (Preferences → Appearance): text font and size, heading
  font (same as the text or a separate family), code font, line spacing, and a maximum
  text width that centers the column. A live sample is shown in the dialog, and changes
  apply to the editor as you make them (Cancel reverts them). The Source-mode editor
  font settings are unchanged.
- **Preview / Reading / PDF**: `~~strikethrough~~`, tables with borders, header band,
  column alignment and striped rows. 2-space and tab-indented nested lists now nest
  properly, and task items show as ☐ / ☑. All of this happens at render time only;
  files are never rewritten.
- New modules: `md_tables.py`, `md_ext.py`, `find_replace.py`, `ui/find_bar.py`.

## 0.6.0
- **Live Preview editor** (new default view): Markdown is styled in place and the
  file stays plain text. Headings by level, bold/italic/strike/highlight/inline code,
  links (`Ctrl`+click opens), quotes with a bar, dot bullets, clickable task checkboxes
  (undoable), horizontal rules, tag chips, fenced code bands with language label,
  plugin-block bands, faded front matter. Syntax markers are hidden except on the cursor
  line / selection. Only the changed lines are re-highlighted. Inline images (WebP etc.)
  render in place and show the raw link when the cursor is on the line.
- **View modes**: Live Preview / Source + Preview / Source Only / Reading (View menu,
  toolbar, `Ctrl+1…4`, Preferences). The choice is remembered (`view_mode`). `Ctrl+P` still toggles the preview pane.
- **Emoji picker**: full Unicode set bundled from emoji-test.txt + CLDR keywords,
  category tabs, search, persisted Recent. Used for journal icons (Choose Emoji…) and
  Edit → **Insert Emoji…** (`Ctrl+.`).
- **Tag Rename History…**: every rename listed with date, scope, journals, file count and
  status. Undo any applied rename, but newer applied renames touching the same entries
  must be undone first. Retention setting (last 20 renames by default, optional max age)
  with automatic cleanup. The manifest now records the scope. Zip export excludes backups
  unless you choose to include them.
- **Tag entries view / Tag Browser** (`Ctrl+Shift+T`): cards for all entries with a tag
  (nested option, this/all journals, newest/oldest), excerpt with the tag highlighted,
  other tags as chips, click to open, **Export These to PDF** (multi-journal aware).
  Opened from the tag panel (double-click), the editor/preview tag menus, or the menu/toolbar.
- Internal: the main window tracks edits with `contentsChange` (a re-highlight is never
  treated as an edit); `export_pdf_pairs()`; `Library.export_zip(include_backups=)`.

## 0.5.0
- **Journal Settings dialog** (Journal → Journal Settings…, ⚙ button and right-click on
  the picker): display name (+ optional safe folder rename), color picker, emoji icon
  grid/free text, per-journal template override with reset, plugins override
  (enable/disable + per-journal schema settings, stored as
  `[overrides.plugin_settings.<id>]`), read-only info (path, entries, images/size,
  date range). OK/Apply/Cancel, applies live.
- `.dailyvibe.toml` edits via **tomlkit** (new dependency): comments, order and unknown
  keys preserved. "Edit Journal Settings File" moved to Journal → Advanced.
- **Rename Tag…** (tag panel context menu, Journal/Edit menus): this journal or all
  journals, nested option, case-insensitive matching, merge warning + front matter
  de-duplication, preview with per-entry counts and sample lines, parser-based
  byte-preserving rewrite, atomic writes, backups in `<journal>/.dailyvibe/backups/`
  and **Undo Last Tag Rename** (only restores files unchanged since).
- Tag panel: parent tags show "(n · m incl. nested)"; front matter with CRLF line
  endings is recognized.

## 0.4.0
- **Multiple journals**: the configured folder is now a library root; each journal is a
  self-contained folder (`<Journal>/YYYY/MM/*.md`, `<Journal>/assets/YYYY/MM/*.webp`,
  relative image links, optional `.dailyvibe.toml` with name/color/icon/overrides).
  Journal picker + Journal menu (new, rename, remove from library, delete to Trash,
  open folder, export zip/PDF, move/copy a single journal). Last journal remembered.
- Everything journal-scoped; search scope *This journal / All journals*.
- One-time, opt-in **migration** of the 0.3 layout into "Personal" (verified backup,
  link rewrite, never overwrites, idempotent, summary).
- Library move replaces the journal-folder move (moves all journals).
- **Tags**: inline `#tags` + front matter, tag panel with AND filter, `tag:` search
  terms, editor autocomplete + highlighting (`hl_tag`), clickable chips in the
  preview, tag filter for date-range PDF export, (mtime, size) tag cache.
- **Weather**: MET Norway Locationforecast backup provider when Open-Meteo fails/429s
  (today … +9 days); provider named in the block; optional `contact` setting.
- Optional extra `trash` (send2trash).

## 0.3.0
- On This Day, streaks, PDF export, app lock, safe journal move/copy, plugin API v1.
