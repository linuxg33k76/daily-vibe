"""Image import: anything Pillow can open -> compressed WebP in assets/."""
from __future__ import annotations

import datetime as dt
import io
import secrets
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic"}


def is_image_file(path: Path | str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def convert_to_webp(
    source: Path | str | bytes | Image.Image,
    dest_dir: Path,
    quality: int = 80,
    max_dimension: int = 1920,
    stem: str | None = None,
) -> Path:
    """Convert `source` (path, raw bytes, or PIL image) to WebP in `dest_dir`.
    Downscales so the longest side <= max_dimension. Returns the new path."""
    if isinstance(source, Image.Image):
        img = source
    elif isinstance(source, (bytes, bytearray)):
        img = Image.open(io.BytesIO(source))
    else:
        img = Image.open(source)
    img.load()
    img = ImageOps.exif_transpose(img)  # respect camera orientation
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() or img.mode == "P" else "RGB")
    if max_dimension and max(img.size) > max_dimension:
        img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or f"{dt.datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    out = dest_dir / f"{stem}.webp"
    img.save(out, "WEBP", quality=int(quality), method=6)
    return out


def markdown_link(image_path: Path, entry_dir: Path, alt: str = "image") -> str:
    """Relative link from the entry's folder (e.g. ../../assets/2026/09/x.webp)."""
    import os
    rel = Path(os.path.relpath(image_path, entry_dir)).as_posix()
    return f"![{alt}]({rel})"
