"""Themes: TOML files with a name, a mode (light/dark) and a color table.

Built-in themes live in daily_vibe/themes/*.toml; user themes can be dropped
into <config dir>/themes/*.toml (same format; same name overrides a built-in).
Adding a theme = adding one file. From the color table we derive:
  * a QPalette (widgets, calendar, list, dialogs)
  * a small Qt stylesheet (borders, calendar nav bar, selection)
  * CSS for the Markdown preview
"""
from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from daily_vibe.config import config_dir

log = logging.getLogger(__name__)
BUILTIN_DIR = Path(__file__).parent / "themes"

REQUIRED_COLORS = (
    "window", "surface", "alt_surface", "text", "muted", "button", "border",
    "accent", "link", "heading", "code_bg", "quote_bg", "quote_text", "weekend",
)
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def user_themes_dir() -> Path:
    return config_dir() / "themes"


# Color helpers ----------------------------------------------------------------
def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(c))) for c in rgb))


def mix(a: str, b: str, t: float) -> str:
    """Blend color a toward b by t (0 = a, 1 = b)."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex([x + (y - x) * t for x, y in zip(ra, rb)])


def luminance(hex_color: str) -> float:
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def readable_on(bg: str) -> str:
    return "#111111" if luminance(bg) > 0.35 else "#ffffff"


# Theme model --------------------------------------------------------------------
@dataclass
class Theme:
    name: str
    mode: str  # "light" | "dark"
    colors: dict[str, str]
    path: Path | None = None
    builtin: bool = True

    @property
    def is_dark(self) -> bool:
        return self.mode == "dark"

    def with_accent(self, accent: str | None) -> "Theme":
        if not accent or not HEX_RE.match(accent):
            return self
        colors = dict(self.colors, accent=accent)
        return Theme(self.name, self.mode, colors, self.path, self.builtin)

    # Editor syntax-highlight colors: optional "hl_*" keys, with fallbacks
    HL_FALLBACKS = {
        "heading": "heading", "emphasis": "text", "code": "quote_text", "code_bg": "code_bg",
        "link": "link", "image": "accent", "list": "accent", "quote": "quote_text", "marker": "muted",
        "tag": "accent",
    }

    def hl(self, key: str) -> str:
        value = self.colors.get(f"hl_{key}")
        if value and HEX_RE.match(value):
            return value
        if key == "marker":
            return mix(self.colors["muted"], self.colors["surface"], 0.35)
        return self.colors[self.HL_FALLBACKS[key]]

    # Derived colors
    @property
    def accent_text(self) -> str:
        return self.colors.get("accent_text") or readable_on(self.colors["accent"])

    @property
    def entry_bg(self) -> str:
        """Calendar background for days that have an entry."""
        return self.colors.get("entry_bg") or mix(self.colors["surface"], self.colors["accent"], 0.28)

    @property
    def entry_fg(self) -> str:
        return self.colors.get("entry_fg") or (
            mix(self.colors["accent"], "#ffffff", 0.45) if self.is_dark else mix(self.colors["accent"], "#000000", 0.45))


def validate(data: dict) -> list[str]:
    errors = []
    if not data.get("name"):
        errors.append("missing name")
    if data.get("mode") not in ("light", "dark"):
        errors.append("mode must be 'light' or 'dark'")
    colors = data.get("colors") or {}
    for key, value in colors.items():
        if key.startswith("hl_") and not HEX_RE.match(str(value)):
            errors.append(f"color {key} must be #rrggbb")
    for key in REQUIRED_COLORS:
        if key not in colors:
            errors.append(f"missing color {key}")
        elif not HEX_RE.match(str(colors[key])):
            errors.append(f"color {key} must be #rrggbb")
    return errors


class ThemeRegistry:
    def __init__(self, extra_dirs: list[Path] | None = None):
        self.dirs = [(BUILTIN_DIR, True), (user_themes_dir(), False)] + [(d, False) for d in (extra_dirs or [])]
        self.themes: dict[str, Theme] = {}
        self.errors: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        self.themes.clear()
        self.errors.clear()
        for directory, builtin in self.dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.toml")):
                try:
                    data = tomllib.loads(path.read_text(encoding="utf-8"))
                    problems = validate(data)
                    if problems:
                        raise ValueError("; ".join(problems))
                    self.themes[data["name"]] = Theme(data["name"], data["mode"], dict(data["colors"]), path, builtin)
                except Exception as exc:
                    log.warning("Bad theme %s: %s", path, exc)
                    self.errors[str(path)] = str(exc)

    def names(self, mode: str | None = None) -> list[str]:
        return sorted(n for n, t in self.themes.items() if mode is None or t.mode == mode)

    def get(self, name: str, mode: str = "light") -> Theme:
        if name in self.themes:
            return self.themes[name]
        fallback = "Default Dark" if mode == "dark" else "Default Light"
        if fallback in self.themes:
            return self.themes[fallback]
        return next(iter(self.themes.values()))


def resolve_mode(mode: str, system_is_dark: bool) -> str:
    if mode in ("light", "dark"):
        return mode
    return "dark" if system_is_dark else "light"


def theme_for_config(registry: ThemeRegistry, appearance: dict, system_is_dark: bool) -> Theme:
    effective = resolve_mode(appearance.get("mode", "system"), system_is_dark)
    name = appearance.get("dark_theme" if effective == "dark" else "light_theme", "")
    return registry.get(name, effective).with_accent(appearance.get("accent") or None)


# Output: CSS / QSS / QPalette -------------------------------------------------------
def table_header_bg(theme: Theme) -> str:
    return mix(theme.colors["surface"], theme.colors["accent"], 0.16)


def table_stripe_bg(theme: Theme) -> str:
    return mix(theme.colors["surface"], theme.colors["text"], 0.09)


def preview_css(theme: Theme, font_size: int = 14) -> str:
    c = theme.colors
    return f"""
body {{ font-size: {font_size}px; line-height: 1.5; color: {c['text']}; background-color: {c['surface']}; }}
h1 {{ font-size: {round(font_size * 1.7)}px; color: {c['heading']}; }}
h2 {{ font-size: {round(font_size * 1.3)}px; color: {c['heading']}; margin-top: 18px; }}
h3 {{ font-size: {round(font_size * 1.1)}px; color: {c['heading']}; }}
a {{ color: {c['link']}; }}
code {{ background-color: {c['code_bg']}; font-family: monospace; }}
pre {{ background-color: {c['code_bg']}; padding: 8px; }}
blockquote {{ color: {c['quote_text']}; background-color: {c['quote_bg']}; margin-left: 0; padding: 4px 10px; }}
table {{ border-collapse: collapse; }}
td, th {{ border: 1px solid {c['border']}; padding: 4px 8px; }}
th {{ background-color: {table_header_bg(theme)}; color: {c['heading']}; }}
del {{ text-decoration: line-through; color: {c['muted']}; }}
hr {{ color: {c['border']}; }}
a.tag {{ color: {theme.accent_text}; background-color: {theme.hl('tag')}; text-decoration: none; font-size: {max(9, font_size - 2)}px; }}
"""


def stylesheet(theme: Theme) -> str:
    c = theme.colors
    return f"""
QPlainTextEdit, QTextBrowser, QListWidget, QLineEdit {{
    border: 1px solid {c['border']}; border-radius: 4px;
    selection-background-color: {c['accent']}; selection-color: {theme.accent_text};
}}
QPlainTextEdit:focus, QLineEdit:focus, QListWidget:focus {{ border: 1px solid {c['accent']}; }}
QListWidget::item {{ padding: 3px; }}
QListWidget::item:selected {{ background: {c['accent']}; color: {theme.accent_text}; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background-color: {c['alt_surface']}; }}
QCalendarWidget QToolButton {{ color: {c['text']}; background: transparent; font-weight: bold; padding: 3px 6px; }}
QCalendarWidget QToolButton:hover {{ background: {c['button']}; border-radius: 4px; }}
QCalendarWidget QAbstractItemView {{ selection-background-color: {c['accent']}; selection-color: {theme.accent_text}; }}
QPushButton {{ padding: 4px 12px; border: 1px solid {c['border']}; border-radius: 4px; background: {c['button']}; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:default {{ border-color: {c['accent']}; }}
QSplitter::handle {{ background: {c['window']}; }}
QStatusBar {{ color: {c['muted']}; }}
QLabel#dateLabel {{ font-weight: bold; padding: 4px; }}
QLabel#hint {{ color: {c['muted']}; }}
QLabel#streak {{ color: {c['heading']}; background: {c['alt_surface']}; border-radius: 4px; padding: 4px 6px; }}
QLabel#panelTitle {{ font-weight: bold; color: {c['heading']}; }}
QLabel#lockTitle {{ font-size: 20px; font-weight: bold; color: {c['heading']}; padding: 8px; }}
QLabel#warning {{ color: {c['weekend']}; }}
QTreeWidget {{ border: 1px solid {c['border']}; border-radius: 4px; }}
QToolTip {{ color: {c['text']}; background: {c['alt_surface']}; border: 1px solid {c['border']}; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; }}
"""


def palette(theme: Theme):
    from PySide6.QtGui import QColor, QPalette

    c = theme.colors
    p = QPalette()
    R = QPalette.ColorRole
    roles = {
        R.Window: c["window"], R.WindowText: c["text"], R.Base: c["surface"],
        R.AlternateBase: c["alt_surface"], R.Text: c["text"], R.Button: c["button"],
        R.ButtonText: c["text"], R.BrightText: c["weekend"], R.Highlight: c["accent"],
        R.HighlightedText: theme.accent_text, R.Link: c["link"], R.LinkVisited: c["link"],
        R.ToolTipBase: c["alt_surface"], R.ToolTipText: c["text"], R.PlaceholderText: c["muted"],
        R.Light: mix(c["button"], "#ffffff", 0.3), R.Midlight: mix(c["button"], "#ffffff", 0.15),
        R.Mid: c["border"], R.Dark: mix(c["button"], "#000000", 0.3), R.Shadow: "#000000",
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    if hasattr(R, "Accent"):
        p.setColor(R.Accent, QColor(c["accent"]))
    disabled = QPalette.ColorGroup.Disabled
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(disabled, role, QColor(c["muted"]))
    return p


def system_prefers_dark() -> bool:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None:
        return False
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:  # Qt < 6.5
        return False


def apply_to_app(app, theme: Theme) -> None:
    app.setStyle("Fusion")  # palette-driven style, consistent across Linux/macOS
    app.setPalette(palette(theme))
    app.setStyleSheet(stylesheet(theme))
