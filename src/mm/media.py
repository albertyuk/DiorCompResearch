"""MediaStore — the single place media paths are constructed.

Everything downloaded at ingest goes to data/runs/{YYYY-MM}/{brand}/media/.
CDN URLs are signed and expire; the PPTX embeds local files only.
"""
from __future__ import annotations

import hashlib
import ipaddress
import mimetypes
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import RUNS_DIR


def _url_is_safe(url: str) -> bool:
    """SSRF guard: only http(s) to hosts that don't resolve to private/loopback
    ranges. Media URLs come from third-party API payloads."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        for info in socket.getaddrinfo(parsed.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast):
                return False
        return True
    except (ValueError, OSError):
        return False

_EXT_BY_CT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
              "image/gif": ".gif", "video/mp4": ".mp4",
              "image/heic": ".heic", "image/heif": ".heif"}

# HEIC/HEIF (served by weibo's app CDN) renders in neither browsers nor
# python-pptx — teach Pillow to open it so we can convert everywhere
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    # weibo tiles large photos into many boxes, tripping libheif's
    # conservative box-count cap ("Maximum number of child boxes (100) in
    # 'ipco' box exceeded") — those are ordinary campaign photos, so lift
    # the limit rather than silently dropping ~10% of a month's images
    pillow_heif.options.DISABLE_SECURITY_LIMITS = True
    HEIF_SUPPORTED = True
except Exception:                                  # pragma: no cover
    HEIF_SUPPORTED = False

HEIC_SUFFIXES = {".heic", ".heif"}


def browser_safe(path: Path) -> Path:
    """Convert a freshly downloaded HEIC/HEIF to JPEG in place (same stem);
    other formats pass through. On any failure the original is kept."""
    if path.suffix.lower() not in HEIC_SUFFIXES:
        return path
    try:
        from PIL import Image
        out = path.with_suffix(".jpg")
        with Image.open(path) as im:
            im.convert("RGB").save(out, "JPEG", quality=92)
        path.unlink(missing_ok=True)
        return out
    except Exception:
        return path


def convert_month_heic(engine, month: str, note=None) -> int:
    """One-time, SEQUENTIAL conversion of every HEIC/HEIF under a month's
    media dirs to JPEG, rewriting the stored paths (posts.media local_path
    + projects.hero_media). Runs before the render's parallel visual
    assembly so tiled-HEIC decodes (huge bitmaps) never happen in four
    workers at once on the 2GB box — that's an OOM kill, which takes the
    whole app (and its activity log) down with it."""
    import json as _json

    from sqlalchemy import select

    from . import db

    month_dir = RUNS_DIR / month
    converted: dict[str, str] = {}
    files = sorted(month_dir.glob("*/media/*")) if month_dir.is_dir() else []
    heics = [f for f in files if f.suffix.lower() in HEIC_SUFFIXES]
    for i, f in enumerate(heics, 1):
        out = browser_safe(f)                      # in place, one at a time
        if out != f:
            converted[str(f)] = str(out)
        if note and (i % 10 == 0 or i == len(heics)):
            note(f"render · converting HEIC media {i}/{len(heics)}…")
    if not converted:
        return 0

    def fix(path: str | None) -> str | None:
        if not path:
            return path
        if path in converted:
            return converted[path]
        # already-deleted original whose .jpg sibling exists (older sweep)
        p = Path(path)
        if p.suffix.lower() in HEIC_SUFFIXES and not p.exists() \
                and p.with_suffix(".jpg").exists():
            return str(p.with_suffix(".jpg"))
        return path

    with engine.begin() as conn:
        for r in conn.execute(select(db.posts.c.post_id, db.posts.c.media)
                              .where(db.posts.c.month == month)).mappings():
            media = _json.loads(r["media"] or "[]")
            changed = False
            for m in media:
                new = fix(m.get("local_path"))
                if new != m.get("local_path"):
                    m["local_path"] = new
                    changed = True
            if changed:
                conn.execute(db.posts.update()
                             .where(db.posts.c.post_id == r["post_id"])
                             .values(media=_json.dumps(media,
                                                       ensure_ascii=False)))
        for r in conn.execute(select(db.projects.c.id,
                                     db.projects.c.hero_media)
                              .where(db.projects.c.month == month)).mappings():
            heroes = _json.loads(r["hero_media"] or "[]")
            fixed = [fix(h) for h in heroes]
            if fixed != heroes:
                conn.execute(db.projects.update()
                             .where(db.projects.c.id == r["id"])
                             .values(hero_media=_json.dumps(
                                 fixed, ensure_ascii=False)))
    return len(converted)


def heic_preview(path: Path) -> Path:
    """For HEIC files already on disk (pre-conversion downloads referenced by
    stored media paths): a cached sibling JPEG for browser display — the
    original stays, so stored paths keep resolving."""
    if path.suffix.lower() not in HEIC_SUFFIXES:
        return path
    out = path.with_suffix(".jpg")
    if out.exists():
        return out
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.convert("RGB").save(out, "JPEG", quality=92)
        return out
    except Exception:
        return path


class MediaStore:
    def __init__(self, month: str):
        self.month = month

    def run_dir(self, brand: str) -> Path:
        return RUNS_DIR / self.month / brand

    def media_dir(self, brand: str) -> Path:
        d = self.run_dir(brand) / "media"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def raw_dir(self, brand: str) -> Path:
        d = self.run_dir(brand) / "raw"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def visuals_dir(self, brand: str) -> Path:
        d = self.run_dir(brand) / "visuals"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def path_for(self, brand: str, url: str, kind: str = "img",
                 ext_hint: str | None = None) -> Path:
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        ext = ext_hint or Path(url.split("?")[0]).suffix or ".bin"
        if len(ext) > 5 or len(ext) < 2:
            ext = ".bin"
        return self.media_dir(brand) / f"{kind}_{h}{ext}"

    def download(self, brand: str, url: str, kind: str = "img",
                 referer: str | None = None, timeout: float = 30.0) -> Path | None:
        """Download url -> local path (idempotent by url hash). Returns None on failure."""
        if not url or not _url_is_safe(url):
            return None
        target = self.path_for(brand, url, kind)
        # If already downloaded under any extension, reuse.
        stem = target.stem
        for existing in target.parent.glob(f"{stem}.*"):
            if existing.stat().st_size > 0:
                return existing
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        if referer:
            headers["Referer"] = referer
        try:
            # tight connect timeout: CDNs that blackhole datacenter IPs must
            # fail in seconds, not stall a whole ingest page for minutes
            with httpx.Client(follow_redirects=True,
                              timeout=httpx.Timeout(timeout, connect=10.0)) as client:
                r = client.get(url, headers=headers)
                r.raise_for_status()
                ct = r.headers.get("content-type", "").split(";")[0].strip()
                ext = _EXT_BY_CT.get(ct) or mimetypes.guess_extension(ct) or target.suffix
                final = target.with_suffix(ext)
                final.write_bytes(r.content)
                return browser_safe(final)
        except Exception:
            return None
