from pathlib import Path

from PIL import Image

from daily_vibe.images import convert_to_webp, markdown_link


def test_png_to_webp_resized(tmp_path):
    src = tmp_path / "big.png"
    Image.new("RGB", (4000, 2000), (200, 30, 30)).save(src)
    out = convert_to_webp(src, tmp_path / "2026" / "09" / "assets", quality=80, max_dimension=1920)
    assert out.suffix == ".webp" and out.parent.name == "assets"
    with Image.open(out) as im:
        assert im.format == "WEBP"
        assert im.size == (1920, 960)
    assert out.stat().st_size < src.stat().st_size


def test_bytes_and_alpha(tmp_path):
    import io
    buf = io.BytesIO()
    Image.new("RGBA", (100, 50), (0, 0, 255, 128)).save(buf, "PNG")
    out = convert_to_webp(buf.getvalue(), tmp_path, stem="pasted")
    assert out.name == "pasted.webp"
    with Image.open(out) as im:
        assert im.size == (100, 50) and im.mode == "RGBA"


def test_markdown_link_is_relative(tmp_path):
    entry_dir = tmp_path / "2026" / "09"
    link = markdown_link(entry_dir / "assets" / "x.webp", entry_dir, "cat")
    assert link == "![cat](assets/x.webp)"
