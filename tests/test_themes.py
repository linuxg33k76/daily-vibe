from daily_vibe import theming
from daily_vibe.theming import ThemeRegistry, theme_for_config

EXPECTED = {"Default Light", "Default Dark", "Nord", "Solarized Light", "Gruvbox Dark", "Catppuccin Mocha"}


def test_all_builtin_themes_load_and_build(qapp):
    reg = ThemeRegistry()
    assert reg.errors == {}
    assert EXPECTED <= set(reg.themes)
    assert reg.names("light") and reg.names("dark")
    for theme in reg.themes.values():
        assert theming.validate({"name": theme.name, "mode": theme.mode, "colors": theme.colors}) == []
        pal = theming.palette(theme)
        assert pal.window().color().name() == theme.colors["window"]
        assert theme.colors["accent"] in theming.stylesheet(theme)
        css = theming.preview_css(theme, 15)
        assert "font-size: 15px" in css and theme.colors["surface"] in css
        # text must be readable on its surface
        assert abs(theming.luminance(theme.colors["text"]) - theming.luminance(theme.colors["surface"])) > 0.3


def test_user_theme_dir_and_bad_file(tmp_path):
    d = tmp_path / "themes"
    d.mkdir()
    src = (theming.BUILTIN_DIR / "nord.toml").read_text().replace('name = "Nord"', 'name = "My Nord"')
    (d / "mine.toml").write_text(src)
    (d / "bad.toml").write_text('name = "Bad"\nmode = "purple"\n[colors]\nwindow = "red"\n')
    reg = ThemeRegistry(extra_dirs=[d])
    assert "My Nord" in reg.themes and not reg.themes["My Nord"].builtin
    assert any("bad.toml" in k for k in reg.errors)


def test_mode_resolution_and_accent():
    reg = ThemeRegistry()
    ap = {"mode": "system", "light_theme": "Solarized Light", "dark_theme": "Gruvbox Dark", "accent": ""}
    assert theme_for_config(reg, ap, system_is_dark=True).name == "Gruvbox Dark"
    assert theme_for_config(reg, ap, system_is_dark=False).name == "Solarized Light"
    assert theme_for_config(reg, dict(ap, mode="dark"), False).name == "Gruvbox Dark"
    t = theme_for_config(reg, dict(ap, mode="light", accent="#ff0000"), True)
    assert t.colors["accent"] == "#ff0000" and t.accent_text == "#ffffff"
    assert reg.get("Nope", "dark").name == "Default Dark"
