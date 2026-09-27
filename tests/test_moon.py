import datetime as dt

from daily_vibe.plugins.moon_phase import moon_info, render

UTC = dt.timezone.utc


def test_known_full_moon():
    info = moon_info(dt.datetime(2024, 1, 25, 17, 54, tzinfo=UTC))
    assert info["phase"] == "Full Moon" and info["illumination"] > 0.98


def test_known_new_moon():
    info = moon_info(dt.datetime(2024, 1, 11, 11, 57, tzinfo=UTC))
    assert info["phase"] == "New Moon" and info["illumination"] < 0.02


def test_first_quarter():
    info = moon_info(dt.datetime(2024, 1, 18, 3, 53, tzinfo=UTC))
    assert info["phase"] == "First Quarter" and 0.4 < info["illumination"] < 0.6 and info["waxing"]


def test_render_contains_emoji_and_percent():
    out = render(dt.date(2024, 1, 25), {})
    assert "🌕" in out and "%" in out
