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
              "image/gif": ".gif", "video/mp4": ".mp4"}


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
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                r = client.get(url, headers=headers)
                r.raise_for_status()
                ct = r.headers.get("content-type", "").split(";")[0].strip()
                ext = _EXT_BY_CT.get(ct) or mimetypes.guess_extension(ct) or target.suffix
                final = target.with_suffix(ext)
                final.write_bytes(r.content)
                return final
        except Exception:
            return None
