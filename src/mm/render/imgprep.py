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
import threading
from pathlib import Path

MAX_EDGE = 1600            # long-edge px — generous for a 14-image grid cell
JPEG_QUALITY = 88
PREP_THRESHOLD = 900_000   # bytes; smaller files embed as-is

# full-res decodes are the render's real memory hog (measured: 4 threads
# preparing a 400-image month drove the process past 1.5GB and OOM-killed
# the 2GB box) — at most two decode at once, and JPEGs decode pre-scaled
_DECODE_GATE = threading.Semaphore(2)

# the only formats python-pptx can embed — anything else (Weibo serves some
# images as WEBP, its app CDN as HEIC) must be converted no matter how small
# the file is
PPTX_FORMATS = {"BMP", "GIF", "JPEG", "PNG", "TIFF", "WMF"}

from ..media import HEIF_SUPPORTED  # noqa: E402  (registers the HEIF opener)


def slide_ready(path: str, cache_dir: Path) -> str | None:
    """Path to embed for `path`: the original when it is already small AND a
    format the deck can embed, else a cached shrunk/converted copy. Returns
    None for files Pillow cannot even open — embedding those would kill the
    whole render (owner report: one .heic aborted the deck), so the caller
    drops that single visual instead."""
    p = Path(path)
    try:
        size = p.stat().st_size
        from PIL import Image, ImageOps
        with Image.open(p) as probe:       # header read only, no decode
            fmt = probe.format
    except Exception:
        return None
    fallback = str(path) if fmt in PPTX_FORMATS else None
    try:
        from PIL import Image, ImageOps
        if size <= PREP_THRESHOLD and fmt in PPTX_FORMATS:
            return str(path)
        key = hashlib.sha1(
            f"{p.resolve()}|{size}|{p.stat().st_mtime_ns}|"
            f"{MAX_EDGE}|{JPEG_QUALITY}".encode()).hexdigest()[:20]
        with _DECODE_GATE, Image.open(p) as im:
            alpha = (im.mode in ("RGBA", "LA")
                     or (im.mode == "P" and "transparency" in im.info))
            out = cache_dir / f"prep_{key}.{'png' if alpha else 'jpg'}"
            if out.exists():
                return str(out)
            if fmt == "JPEG":
                # decode at a reduced DCT scale — a fraction of the memory
                # of a full-res decode, and we shrink to MAX_EDGE anyway
                im.draft("RGB", (MAX_EDGE, MAX_EDGE))
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
        return fallback
