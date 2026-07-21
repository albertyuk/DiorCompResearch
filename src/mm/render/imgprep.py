"""Slide-ready image preparation.

Ingest deliberately keeps ORIGINAL-resolution files (largest Weibo variant,
HQ uploads up to 30MB) because the review UI wants them — but a deck grid
cell shows at most ~2 inches of image. Embedding originals bloats the PPTX,
slows python-pptx save, and multiplies the LibreOffice QA raster time. So
slides embed a downscaled/recompressed copy from a content-addressed cache;
the originals on disk are never touched.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

MAX_EDGE = 1600            # long-edge px — generous for a 14-image grid cell
JPEG_QUALITY = 88
PREP_THRESHOLD = 900_000   # bytes; smaller files embed as-is

# the only formats python-pptx can embed — anything else (Weibo serves some
# images as WEBP) must be converted no matter how small the file is
PPTX_FORMATS = {"BMP", "GIF", "JPEG", "PNG", "TIFF", "WMF"}


def slide_ready(path: str, cache_dir: Path) -> str:
    """Path to embed for `path`: the original when it is already small AND a
    format the deck can embed, else a cached shrunk/converted copy. Any
    failure falls back to the original — preparing images must never cost a
    slide."""
    p = Path(path)
    try:
        size = p.stat().st_size
        from PIL import Image, ImageOps
        with Image.open(p) as probe:       # header read only, no decode
            fmt = probe.format
        if size <= PREP_THRESHOLD and fmt in PPTX_FORMATS:
            return str(path)
        key = hashlib.sha1(
            f"{p.resolve()}|{size}|{p.stat().st_mtime_ns}|"
            f"{MAX_EDGE}|{JPEG_QUALITY}".encode()).hexdigest()[:20]
        with Image.open(p) as im:
            alpha = (im.mode in ("RGBA", "LA")
                     or (im.mode == "P" and "transparency" in im.info))
            out = cache_dir / f"prep_{key}.{'png' if alpha else 'jpg'}"
            if out.exists():
                return str(out)
            im = ImageOps.exif_transpose(im)
            im.thumbnail((MAX_EDGE, MAX_EDGE))     # shrink only, keep ratio
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + f".tmp{os.getpid()}")
            if alpha:
                im.save(tmp, "PNG", optimize=True)
            else:
                im.convert("RGB").save(tmp, "JPEG", quality=JPEG_QUALITY,
                                       optimize=True, progressive=True)
            os.replace(tmp, out)                   # atomic under parallelism
        return str(out)
    except Exception:
        return str(path)
