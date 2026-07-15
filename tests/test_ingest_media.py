"""Concurrent per-post media downloads: results map back to the right slots,
duplicate URLs fetch once, and failures stay silent (local_path=None)."""
from pathlib import Path

from mm import ingest
from mm.media import MediaStore


def test_download_post_media_parallel_mapping(tmp_path, monkeypatch):
    calls = []

    def fake_download(self, brand, url, kind="img", referer=None, timeout=30.0):
        calls.append((url, kind))
        if "fail" in url:
            return None
        return tmp_path / f"{kind}_{url.rsplit('/', 1)[-1]}"

    monkeypatch.setattr(MediaStore, "download", fake_download)
    post = {
        "media": [{"url": "http://cdn/a.jpg", "kind": "image"},
                  {"url": None, "kind": "image"},
                  {"url": "http://cdn/fail.jpg", "kind": "image"},
                  {"url": "http://cdn/a.jpg", "kind": "image"}],
        "author_avatar": "http://cdn/av.jpg",
    }
    ingest._download_post_media(MediaStore("2026-06"), "lv", post, "http://r/")

    assert post["media"][0]["local_path"].endswith("img_a.jpg")
    assert post["media"][1]["local_path"] is None
    assert post["media"][2]["local_path"] is None      # failed → None, no raise
    assert post["media"][3]["local_path"].endswith("img_a.jpg")
    assert post["author_avatar_path"].endswith("avatar_av.jpg")
    # duplicate media URL downloaded exactly once; avatar kept its own kind
    assert calls.count(("http://cdn/a.jpg", "img")) == 1
    assert ("http://cdn/av.jpg", "avatar") in calls


def test_download_post_media_no_avatar(tmp_path, monkeypatch):
    monkeypatch.setattr(MediaStore, "download",
                        lambda self, brand, url, kind="img", referer=None,
                        timeout=30.0: Path(tmp_path / "x.jpg"))
    post = {"media": [], "author_avatar": None}
    ingest._download_post_media(MediaStore("2026-06"), "lv", post, "http://r/")
    assert post["author_avatar_path"] is None
