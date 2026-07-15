"""Phase 6 post visuals — the deck embeds screenshots of posts, not bare images.

- live mode (default for Weibo): Playwright screenshot of the m.weibo.cn post
  page cropped to the post card; graceful per-post failure → card fallback.
- card mode (automatic fallback; default for RED/Douyin/WeChat, which sit
  behind login/region walls): render an HTML post-card (avatar, account name,
  date, caption, first image) → PNG via Playwright.
"""
from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
from pathlib import Path

from ..media import MediaStore

CARD_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#fff; font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif; }}
.card {{ width:600px; background:#fff; padding:24px 24px 18px; }}
.head {{ display:flex; align-items:center; gap:12px; margin-bottom:14px; }}
.avatar {{ width:48px; height:48px; border-radius:50%; object-fit:cover; background:#eee; }}
.avatar-ph {{ width:48px; height:48px; border-radius:50%; background:#e8e8e8; }}
.name {{ font-size:16px; font-weight:600; color:#eb7350; }}
.meta {{ font-size:12px; color:#939393; margin-top:3px; }}
.caption {{ font-size:15px; line-height:1.55; color:#1a1a1a; white-space:pre-wrap; word-break:break-word; margin-bottom:14px; }}
.pic {{ width:100%; max-height:640px; object-fit:cover; border-radius:4px; display:block; }}
.badge {{ margin-top:12px; font-size:11px; color:#b0b0b0; letter-spacing:1px; }}
</style></head><body>
<div class="card" id="card">
  <div class="head">
    {avatar_html}
    <div><div class="name">{author}</div><div class="meta">{date}</div></div>
  </div>
  <div class="caption">{caption}</div>
  {image_html}
  <div class="badge">{platform}</div>
</div>
</body></html>"""

PLATFORM_BADGE = {"weibo": "WEIBO", "xhs": "XIAOHONGSHU · RED", "douyin": "DOUYIN",
                  "wechat_mp": "WECHAT 公众号", "wechat_channels": "WECHAT 视频号"}

# candidate selectors for the m.weibo.cn post card
_WEIBO_CARD_SELECTORS = [".card.m-panel", ".weibo-detail", ".card-wrap", "#app"]


def _data_uri(path: str | None) -> str | None:
    if not path or not Path(path).is_file():
        return None
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    try:
        b64 = base64.b64encode(Path(path).read_bytes()).decode()
        return f"data:{mime};base64,{b64}"
    except OSError:
        return None


class VisualFactory:
    def __init__(self, month: str, mode: str = "live", headless: bool = True):
        self.month = month
        self.mode = mode
        self.store = MediaStore(month)
        self._pw = None
        self._browser = None
        self._page = None
        self.headless = headless

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=self.headless)
        except Exception:
            exe = self._find_chromium()
            if not exe:
                raise
            self._browser = self._pw.chromium.launch(headless=self.headless,
                                                     executable_path=exe)
        self._page = self._new_page()
        return self

    def _new_page(self):
        return self._browser.new_page(
            viewport={"width": 700, "height": 1200},
            user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
                        "Mobile/15E148 Safari/604.1"))

    def _reset_page(self):
        """A failed navigation can leave the page unusable — recreate it so a
        live-screenshot failure never poisons the card fallback."""
        try:
            self._page.close()
        except Exception:
            pass
        self._page = self._new_page()

    @staticmethod
    def _find_chromium() -> str | None:
        """Fallback for environments whose pre-installed Chromium doesn't match
        the pinned Playwright build (normal setups just `playwright install`)."""
        import glob
        import os
        roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "",
                 os.path.expanduser("~/.cache/ms-playwright"), "/opt/pw-browsers"]
        patterns = ["chromium-*/chrome-linux/chrome",
                    "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
                    "chromium"]
        for root in roots:
            if not root or not os.path.isdir(root):
                continue
            for pat in patterns:
                hits = sorted(glob.glob(os.path.join(root, pat)), reverse=True)
                for h in hits:
                    if os.path.isfile(h) and os.access(h, os.X_OK):
                        return h
        return None

    def __exit__(self, *exc):
        for closer in (self._browser, ):
            try:
                closer.close()
            except Exception:
                pass
        try:
            self._pw.stop()
        except Exception:
            pass

    # -- live weibo screenshot -------------------------------------------------

    def _weibo_mobile_url(self, url: str) -> str | None:
        m = re.search(r"weibo\.com/\d+/([A-Za-z0-9]+)", url or "")
        return f"https://m.weibo.cn/detail/{m.group(1)}" if m else None

    def live_screenshot(self, brand: str, post: dict) -> Path | None:
        url = self._weibo_mobile_url(post.get("url") or "")
        if not url:
            return None
        out = self.store.visuals_dir(brand) / f"live_{post['post_id'].replace(':', '_')}.png"
        if out.exists():
            return out
        try:
            self._page.goto(url, timeout=20000, wait_until="domcontentloaded")
            self._page.wait_for_timeout(2500)
            for sel in _WEIBO_CARD_SELECTORS:
                el = self._page.query_selector(sel)
                if el:
                    box = el.bounding_box()
                    if box and box["width"] > 100 and box["height"] > 150:
                        el.screenshot(path=str(out))
                        return out
            return None
        except Exception:
            self._reset_page()
            return None

    # -- card renderer -----------------------------------------------------------

    def render_card(self, brand: str, post: dict) -> Path | None:
        out = self.store.visuals_dir(brand) / f"card_{post['post_id'].replace(':', '_')}.png"
        if out.exists():
            return out
        media = post.get("media") or []
        if isinstance(media, str):
            media = json.loads(media)
        first_img = next((m.get("local_path") for m in media if m.get("local_path")), None)
        avatar = post.get("author_avatar_path")
        # data: URIs — headless Chromium blocks file:// subresources on
        # set_content pages (about:blank origin)
        avatar_uri = _data_uri(avatar)
        img_uri = _data_uri(first_img)
        avatar_html = (f'<img class="avatar" src="{avatar_uri}">' if avatar_uri
                       else '<div class="avatar-ph"></div>')
        image_html = (f'<img class="pic" src="{img_uri}">' if img_uri else "")
        caption = html.escape((post.get("caption") or "")[:400])
        doc = CARD_TEMPLATE.format(
            avatar_html=avatar_html, image_html=image_html,
            author=html.escape(post.get("author_name") or ""),
            date=html.escape((post.get("created_at") or "")[:10]),
            caption=caption,
            platform=PLATFORM_BADGE.get(post.get("platform"), ""))
        for attempt in range(2):
            try:
                self._page.set_content(doc, wait_until="load")
                self._page.wait_for_timeout(250)
                el = self._page.query_selector("#card")
                el.screenshot(path=str(out))
                return out
            except Exception:
                self._reset_page()
        return None

    def visual_for_post(self, brand: str, post: dict) -> Path | None:
        """live → card fallback for weibo; card for everything else."""
        if post.get("platform") == "weibo" and self.mode == "live":
            shot = self.live_screenshot(brand, post)
            if shot:
                return shot
        return self.render_card(brand, post)

    def video_cover_for_post(self, brand: str, post: dict) -> Path | None:
        media = post.get("media") or []
        if isinstance(media, str):
            media = json.loads(media)
        cover = next((m.get("local_path") for m in media
                      if m.get("kind") == "video_cover" and m.get("local_path")), None)
        return Path(cover) if cover else None
