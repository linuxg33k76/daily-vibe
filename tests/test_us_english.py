"""US English regression guard (0.8.1): the app's source strings, comments, bundled
themes and docs must not contain common British spellings.

A line that genuinely needs a British spelling (e.g. a backward-compatible legacy
config key or alias) must carry the marker ``legacy-spelling`` on the same line.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOW_MARKER = "legacy-spelling"

BRITISH = re.compile(
    r"\b("
    r"(col|behavi|flav|hon|lab|neighb|hum|rum|harb|vap|sav|arm|endeav|od|vig|splend|parl|glam|tum)our(s|ed|ing|ful|less|able|ite|ites)?"
    r"|favourite?s?"
    r"|grey(s|ed|ing|ish|scale)?"
    r"|centr(e|es|ed|ing)"
    r"|(theat|fib|lit|met|calib|lust|sab|spect)res?"
    r"|(normal|custom|recogn|initial|summar|optim|synchron|serial|priorit|minim|maxim|final|author|visual"
    r"|categor|organ|real|apolog|util|emphas|standard|special|local|capital|sanit|token|memor|personal"
    r"|random|character|parameter|digit|stabil|neutral|critic|central|general)is(e|es|ed|ing|ation|ations|er|ers)"
    r"|analys(e|ed|ing)"
    r"|licences?|defences?|offences?|pretences?"
    r"|cancell(ed|ing)|travell(ed|ing|er)|modell(ed|ing)|labell(ed|ing)|levell(ed|ing)|signall(ed|ing)|fuell(ed|ing)"
    r"|catalogues?|dialogues?|analogue|programmes?|practis(e|ed|es|ing)"
    r"|fulfil|enrol|whilst|amongst|judgement|artefacts?|tonnes?|mould(s|ed|ing)?"
    r"|cheques?|tyres?|aluminium|manoeuvr\w*|sceptic\w*"
    r")\b",
    re.IGNORECASE,
)


def _files() -> list[Path]:
    files = sorted((ROOT / "daily_vibe").rglob("*.py"))
    files += sorted((ROOT / "daily_vibe" / "themes").glob("*.toml"))
    files += [ROOT / "README.md", ROOT / "pyproject.toml"]
    files += sorted((ROOT / "docs").rglob("*.md"))
    files += sorted((ROOT / "examples").rglob("*.py"))
    return [f for f in files if f.is_file() and "__pycache__" not in f.parts]


def _hits(text: str) -> list[tuple[int, str]]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        # underscores -> spaces so identifiers like _rehighlight_neighbours are checked too
        out += [(n, m.group(0)) for m in BRITISH.finditer(line.replace("_", " "))]
    return out


def test_pattern_catches_british_and_spares_us():
    british = ("Colour: colours coloured behaviour centred centre favourite grey normalise "
               "recognised initialisation organise analyse licence cancelled labelled catalogue "
               "dialogue programme whilst amongst judgement artefact honour neighbours metre")
    us = ("Color: colors colored behavior centered center favorite gray normalize recognized "
          "initialization organize analyze analyses license canceled labeled catalog dialog "
          "QDialog QColor programmer while among judgment artifact honor neighbors parameter "
          "emphasis characteristic organism realism specialist")
    found = {m.group(0).lower() for m in BRITISH.finditer(british)}
    assert len(found) == len(british.split()), set(w.lower() for w in british.split()) - found
    assert not [m.group(0) for m in BRITISH.finditer(us)]


def test_no_british_spellings_in_source_and_docs():
    problems = []
    for f in _files():
        for n, word in _hits(f.read_text(encoding="utf-8")):
            problems.append(f"{f.relative_to(ROOT)}:{n}: {word}")
    assert not problems, "British spellings found (use US English):\n" + "\n".join(problems)


def test_no_british_spellings_in_test_names():
    problems = []
    for f in sorted((ROOT / "tests").glob("test_*.py")):
        for name in re.findall(r"^\s*def (test_\w+)", f.read_text(encoding="utf-8"), re.M):
            if BRITISH.search(name.replace("_", " ")):
                problems.append(f"{f.name}: {name}")
    assert not problems, problems


# --- Backward compatibility for renamed keys / identifiers ------------------------------
LEGACY_META = """# my journal (hand-written)
name = "Work"
colour = "#ff8800"  # my favorite orange
icon = "💼"
"""  # legacy-spelling


def test_legacy_british_color_key_is_read_as_fallback(tmp_path):
    from daily_vibe.journal_meta import load_meta
    from daily_vibe.storage import Journal
    (tmp_path / ".dailyvibe.toml").write_text(LEGACY_META, encoding="utf-8")
    meta = load_meta(tmp_path)
    assert meta["color"] == "#ff8800" and "colour" not in meta  # legacy-spelling
    assert Journal(tmp_path).meta["color"] == "#ff8800"


def test_us_key_wins_over_legacy_key(tmp_path):
    from daily_vibe.journal_meta import load_meta
    (tmp_path / ".dailyvibe.toml").write_text('color = "#111111"\ncolour = "#222222"\n')  # legacy-spelling
    assert load_meta(tmp_path)["color"] == "#111111"


def test_legacy_key_migrated_on_save_keeping_comments(tmp_path):
    from daily_vibe import journal_meta
    (tmp_path / ".dailyvibe.toml").write_text(LEGACY_META, encoding="utf-8")
    journal_meta.update_meta(tmp_path, {"icon": "📓"})       # unrelated edit still migrates
    text = (tmp_path / ".dailyvibe.toml").read_text(encoding="utf-8")
    assert "colour" not in text and 'color = "#ff8800"' in text  # legacy-spelling
    if journal_meta.preserves_comments():
        assert "# my journal (hand-written)" in text
    assert journal_meta.load_meta(tmp_path) == {"name": "Work", "color": "#ff8800", "icon": "📓"}
    # changing the color via the app writes only the US key
    journal_meta.update_meta(tmp_path, {"color": "#00ff00"})
    assert journal_meta.load_meta(tmp_path)["color"] == "#00ff00"
    assert "colour" not in (tmp_path / ".dailyvibe.toml").read_text()  # legacy-spelling


def test_legacy_key_migrated_without_tomlkit(tmp_path, monkeypatch):
    from daily_vibe import journal_meta
    monkeypatch.setattr(journal_meta, "tomlkit", None)
    (tmp_path / ".dailyvibe.toml").write_text(LEGACY_META, encoding="utf-8")
    journal_meta.update_meta(tmp_path, {"name": "Work 2"})
    text = (tmp_path / ".dailyvibe.toml").read_text(encoding="utf-8")
    assert "colour" not in text and journal_meta.load_meta(tmp_path)["color"] == "#ff8800"  # legacy-spelling


def test_save_meta_normalizes_legacy_key(tmp_path):
    from daily_vibe import journal_meta
    journal_meta.save_meta(tmp_path, {"name": "X", "colour": "#abcdef"})  # legacy-spelling
    assert journal_meta.load_meta(tmp_path) == {"name": "X", "color": "#abcdef"}
    assert "colour" not in (tmp_path / ".dailyvibe.toml").read_text()  # legacy-spelling


def test_relocate_legacy_cancel_alias(tmp_path):
    from daily_vibe import relocate
    src, dst = tmp_path / "src", tmp_path / "dst"
    for d in ("2026/09/2026-09-01.md", "2026/09/2026-09-02.md"):
        (src / d).parent.mkdir(parents=True, exist_ok=True)
        (src / d).write_text("# x\n")
    calls = []
    r = relocate.transfer(src, dst, "copy", progress=lambda *a: calls.append(a),
                          cancelled=lambda: len(calls) >= 1)  # legacy-spelling
    assert r.canceled and r.cancelled and len(r.transferred) == 1  # legacy-spelling
    r.cancelled = False  # legacy-spelling
    assert r.canceled is False


def test_journal_settings_shows_us_color_label_and_legacy_value(qapp, tmp_path):
    from PySide6.QtWidgets import QLabel
    from daily_vibe.config import Config
    from daily_vibe.library import Library
    from daily_vibe.ui.main_window import MainWindow
    root = tmp_path / "Lib"
    j = Library(root).create("Personal")
    (j.root / ".dailyvibe.toml").write_text(LEGACY_META.replace('"Work"', '"Personal"'), encoding="utf-8")
    cfg = Config.load()
    cfg.journal_root = root
    cfg.data["plugins"]["enabled"] = []
    cfg.data["last_journal"] = "Personal"
    cfg.save()
    win = MainWindow(cfg)
    try:
        dlg = win.open_journal_settings()
        labels = [w.text() for w in dlg.findChildren(QLabel)]
        assert "Color:" in labels and not [t for t in labels if BRITISH.search(t)]
        assert dlg._color == "#ff8800"
        dlg.close()
    finally:
        win.close()
        win.runner.shutdown(1000)
        win.deleteLater()
        qapp.processEvents()
