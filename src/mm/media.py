"""MediaStore — the single place media paths are constructed.

Everything downloaded at ingest goes to data/runs/{YYYY-MM}/{brand}/media/.
CDN URLs are signed and expire; the PPTX embeds local files only.
"""
from __future__ import annotations

import hashlib
import ipaddress
import mimetypes
import re
import socket
from concurrent.futures import ThreadPoolExecutor
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

# Weibo's app CDN serves HEIC bytes under h-prefixed size buckets
# (…sinaimg.cn/hlarge/xxx.jpg → content-type image/heic). The SAME image is
# published as plain JPEG under the un-prefixed bucket (…/large/…), verified
# live 2026-07-21 — so the fast path for HEIC is a re-download, not a decode.
_JPEG_BUCKET = re.compile(
    r"^(large|largest|mw\d+|orj\d+|original|oslarge|bmiddle|middle"
    r"|thumbnail|small)$")


def jpeg_variant(url: str) -> str | None:
    """URL of the plain-JPEG size bucket for a sinaimg h-bucket url, or None
    when the url isn't one (non-weibo hosts, already-JPEG buckets, videos)."""
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        if not (host.endswith(".sinaimg.cn") or host == "sinaimg.cn"):
            return None
        parts = p.path.split("/")
        if len(parts) < 3:
            return None
        bucket = parts[1]
        if not bucket.startswith("h") or not _JPEG_BUCKET.match(bucket[1:]):
            return None
        return url.replace(f"/{bucket}/", f"/{bucket[1:]}/", 1)
    except Exception:
        return None


_DL_HEADERS = {"User-Agent":
               "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def _refetch_jpeg(url: str, heic_path: Path,
                  referer: str = "https://weibo.com/",
                  client: httpx.Client | None = None) -> Path | None:
    """Fetch the JPEG original for a HEIC file already on disk, saving it as
    the sibling .jpg. Tries the un-prefixed bucket first, then the stored
    url itself; only accepts real JPEG bytes. Returns None on any failure —
    the caller falls back to local decoding. Pass a shared client when
    fetching in bulk (keeps connections alive across files)."""
    headers = dict(_DL_HEADERS, Referer=referer)
    candidates = [u for u in (jpeg_variant(url), url) if u]

    def attempt(c: httpx.Client) -> Path | None:
        for cu in candidates:
            try:
                r = c.get(cu, headers=headers)
                r.raise_for_status()
            except Exception:
                continue
            if r.content[:3] == b"\xff\xd8\xff":           # JPEG magic
                out = heic_path.with_suffix(".jpg")
                out.write_bytes(r.content)
                return out
        return None

    try:
        if client is not None:
            return attempt(client)
        with httpx.Client(follow_redirects=True,
                          timeout=httpx.Timeout(30.0, connect=10.0)) as c:
            return attempt(c)
    except Exception:
        return None


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


HEIC_MAX_PIXELS = 80_000_000       # decode cap: ~320MB RGBA on the 2GB box
HEIC_FILE_TIMEOUT = 60.0           # seconds a single file may take


def _heic_worker(inq, outq):        # runs in a KILLABLE child process
    import pillow_heif
    pillow_heif.register_heif_opener()
    pillow_heif.options.DISABLE_SECURITY_LIMITS = True
    from PIL import Image
    while True:
        path = inq.get()
        if path is None:
            return
        try:
            heif = pillow_heif.open_heif(path)     # parse only, no decode
            w, h = heif.size
            if w * h > HEIC_MAX_PIXELS:
                outq.put(("too_large", path))
                continue
            out = Path(path).with_suffix(".jpg")
            with Image.open(path) as im:
                im.convert("RGB").save(out, "JPEG", quality=92)
            Path(path).unlink(missing_ok=True)
            outq.put(("ok", str(out)))
        except Exception as e:
            outq.put(("error", str(e)[:160]))


class _HeicPool:
    """One persistent child process converts files; a file that hangs, OOMs
    or crashes kills only the CHILD — the parent times out, respawns it and
    moves on. This is what makes a 560-file sweep safe: one pathological
    tile-bomb (owner report: stuck at 130/560) can no longer stall or take
    down the app."""

    def __init__(self):
        import multiprocessing
        self.ctx = multiprocessing.get_context("spawn")
        self._start()

    def _start(self):
        self.inq = self.ctx.Queue()
        self.outq = self.ctx.Queue()
        self.proc = self.ctx.Process(target=_heic_worker,
                                     args=(self.inq, self.outq), daemon=True)
        self.proc.start()

    def convert(self, path: Path, timeout: float = HEIC_FILE_TIMEOUT):
        import queue as _q
        import time as _t
        self.inq.put(str(path))
        deadline = _t.monotonic() + timeout
        while _t.monotonic() < deadline:
            try:
                return self.outq.get(timeout=1.0)
            except _q.Empty:
                if not self.proc.is_alive():       # OOM-killed child
                    self._start()
                    return ("died", str(path))
        self.proc.kill()
        self.proc.join()
        self._start()
        return ("timeout", str(path))

    def close(self):
        try:
            self.inq.put(None)
            self.proc.join(timeout=3)
        except Exception:
            pass
        if self.proc.is_alive():
            self.proc.kill()


REFETCH_WORKERS = 8                # I/O-bound CDN fetches, tiny memory cost


def convert_month_heic(engine, month: str, note=None) -> int:
    """One-time replacement of every HEIC/HEIF under a month's media dirs
    with JPEG, rewriting the stored paths (posts.media local_path +
    projects.hero_media).

    Fast path (seconds, not hours): the CDN publishes the same image as
    plain JPEG under the un-prefixed size bucket, so each file is simply
    RE-DOWNLOADED in parallel — no decoding. Only files whose re-fetch
    fails (dead url, no stored url) fall back to decoding in a killable
    child process with a pixel cap and hard timeout; unconvertible ones are
    renamed *.skip so nothing ever decodes them again. The sweep ALWAYS
    finishes."""
    import json as _json

    from sqlalchemy import select

    from . import db

    month_dir = RUNS_DIR / month
    converted: dict[str, str] = {}
    skipped = 0
    files = sorted(month_dir.glob("*/media/*")) if month_dir.is_dir() else []
    heics = [f for f in files if f.suffix.lower() in HEIC_SUFFIXES]

    leftovers: list[Path] = []
    if heics:
        url_by_path: dict[str, str] = {}
        with engine.connect() as conn:
            for r in conn.execute(select(db.posts.c.media)
                                  .where(db.posts.c.month == month)):
                for m in _json.loads(r[0] or "[]"):
                    lp, u = m.get("local_path"), m.get("url")
                    if lp and u:
                        url_by_path[lp] = u

        def work(shared: httpx.Client, f: Path) -> tuple[Path, Path | None]:
            url = url_by_path.get(str(f))
            return f, (_refetch_jpeg(url, f, client=shared) if url else None)

        done = 0
        with httpx.Client(follow_redirects=True,
                          timeout=httpx.Timeout(30.0, connect=10.0)) \
                as shared, \
                ThreadPoolExecutor(max_workers=REFETCH_WORKERS) as ex:
            for f, out in ex.map(lambda f: work(shared, f), heics):
                done += 1
                if note:
                    note(f"render · refetching JPEG originals "
                         f"{done}/{len(heics)}…")
                if out is not None:
                    f.unlink(missing_ok=True)
                    converted[str(f)] = str(out)
                else:
                    leftovers.append(f)

    if leftovers:
        pool = _HeicPool()
        try:
            for i, f in enumerate(leftovers, 1):
                if note:
                    note(f"render · converting leftover HEIC "
                         f"{i}/{len(leftovers)}…")
                status, payload = pool.convert(f)
                if status == "ok":
                    converted[str(f)] = payload
                else:
                    skipped += 1
                    try:                    # quarantine — never decode again
                        f.rename(f.with_name(f.name + ".skip"))
                    except OSError:
                        pass
        finally:
            pool.close()
    if note and skipped:
        note(f"render · warning: {skipped} HEIC file(s) unconvertible "
             f"(unfetchable + oversized/corrupt) — skipped, their slides "
             f"lose that image")
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
        # If already downloaded under any extension, reuse (never a
        # quarantined *.skip file).
        stem = target.stem
        for existing in target.parent.glob(f"{stem}.*"):
            if existing.suffix != ".skip" and existing.stat().st_size > 0:
                return existing
        headers = dict(_DL_HEADERS)
        if referer:
            headers["Referer"] = referer
        # h-bucket sinaimg urls serve HEIC — fetch the JPEG bucket first so
        # we never store (then have to convert) HEIC at all
        candidates = [u for u in (jpeg_variant(url), url) if u]
        try:
            # tight connect timeout: CDNs that blackhole datacenter IPs must
            # fail in seconds, not stall a whole ingest page for minutes
            with httpx.Client(follow_redirects=True,
                              timeout=httpx.Timeout(timeout, connect=10.0)) as client:
                r = None
                for cu in candidates:
                    try:
                        r = client.get(cu, headers=headers)
                        r.raise_for_status()
                        break
                    except Exception:
                        r = None
                if r is None:
                    return None
                ct = r.headers.get("content-type", "").split(";")[0].strip()
                ext = _EXT_BY_CT.get(ct) or mimetypes.guess_extension(ct) or target.suffix
                final = target.with_suffix(ext)
                final.write_bytes(r.content)
                return browser_safe(final)
        except Exception:
            return None
