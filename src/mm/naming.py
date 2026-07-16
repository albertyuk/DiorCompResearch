"""Naming/notation helpers: pinyin display names, phase-suffix standardization,
SOCIAL column assembly, relationship display."""
from __future__ import annotations

import re

from pypinyin import lazy_pinyin

from .config import DISPLAY_PLATFORMS, PLATFORM_TO_DISPLAY

# Known campaign-phase suffixes (Decision Record: standardize to " — SUFFIX").
PHASE_SUFFIXES = ["TEASER", "CELEBS", "EVENT", "MAKING OF", "VIP ARRIVAL",
                  "SCENOGRAPHY", "ARRIVAL", "EVENT RECAP"]

_SUFFIX_RE = re.compile(
    r"\s*[-–—]{1,2}\s*(" + "|".join(re.escape(s) for s in PHASE_SUFFIXES) + r")\s*$",
    re.IGNORECASE)


def split_phase_suffix(title: str) -> tuple[str, str | None]:
    """'LA GALERIE DU 19M -- TEASER' -> ('LA GALERIE DU 19M', 'TEASER')."""
    m = _SUFFIX_RE.search(title)
    if not m:
        return title.strip(), None
    return title[: m.start()].strip(), m.group(1).upper()


def format_title(base: str, phase_suffix: str | None) -> str:
    base = re.sub(r"\s+", " ", base).strip().upper()
    if phase_suffix:
        return f"{base} — {phase_suffix.strip().upper()}"
    return base


def caps_pinyin(name_cn: str) -> str:
    """王一博 -> WANG YIBO (surname split heuristic: first char = surname)."""
    syllables = lazy_pinyin(name_cn)
    if not syllables:
        return name_cn.upper()
    surname = syllables[0]
    given = "".join(syllables[1:])
    parts = [surname] + ([given] if given else [])
    return " ".join(p.upper() for p in parts)


def display_name(name_en: str | None, name_cn: str | None) -> str:
    if name_en and name_en.strip():
        return name_en.strip().upper()
    if name_cn and name_cn.strip():
        return caps_pinyin(name_cn.strip())
    return ""


def relation_display(relation: str | None, occupation: str | None,
                     verified: bool) -> str:
    """BRAND AMBASSADOR / BRAND FRIEND verified as-is; otherwise occupation,
    with trailing ' ?' when unverified (Decision Record)."""
    label = (relation or occupation or "").strip().upper()
    if not label:
        label = "CELEBRITY"
        verified = False
    if not verified:
        label = f"{label} ?"
    return label


def social_column(platforms_present: set[str] | list[str]) -> str:
    """Ingestion platform keys or display names -> 'WEIBO/RED/WECHAT/DOUYIN' subset."""
    display = set()
    for p in platforms_present:
        display.add(PLATFORM_TO_DISPLAY.get(p, p.upper()))
    return "/".join(p for p in DISPLAY_PLATFORMS if p in display)


def assets_label(has_photo: bool, has_video: bool) -> str:
    if has_photo and has_video:
        return "PHOTO VIDEO"
    if has_video:
        return "VIDEO"
    return "PHOTO"


# Chinese title words -> deck relationship vocabulary (caption extraction, §6 Phase 5).
CN_TITLE_MAP = [
    ("全球代言人", "BRAND AMBASSADOR"),
    ("品牌代言人", "BRAND AMBASSADOR"),
    ("代言人", "BRAND AMBASSADOR"),
    ("品牌大使", "BRAND AMBASSADOR"),
    ("形象大使", "BRAND AMBASSADOR"),
    ("大使", "BRAND AMBASSADOR"),
    ("品牌挚友", "BRAND FRIEND"),
    ("品牌好友", "BRAND FRIEND"),
    ("挚友", "BRAND FRIEND"),
]


def map_cn_title(raw_title: str) -> str | None:
    for cn, en in CN_TITLE_MAP:
        if cn in raw_title:
            return en
    return None


# Deterministic beauty signal — defense-in-depth beside the LLM filter
# (Chanel's flagship account mixes fashion and beauty). Owner directive
# 2026-07 (supersedes the original Decision Record): perfume/fragrance is OUT
# of scope, same as makeup/skincare.
_FRAGRANCE_TERMS = ["香水", "香氛", "淡香", "浓香", "香调", "中性香",
                    "fragrance", "parfum", "eau de", "scent", "cologne"]
_MAKEUP_SKINCARE_TERMS = ["彩妆", "美妆", "口红", "唇膏", "唇釉", "粉底", "粉饼",
                          "眼影", "睫毛膏", "腮红", "护肤", "精华液", "精华露",
                          "面霜", "眼霜", "乳液", "洁面", "面膜", "防晒霜",
                          "气垫", "遮瑕", "眉笔", "妆容教程", "lipstick",
                          "foundation", "mascara", "skincare", "serum",
                          "moisturizer", "makeup"]


def cosmetics_signal(caption: str) -> str | None:
    """'fragrance' or 'makeup_skincare' (both are policy DROP), or None."""
    text = (caption or "").lower()
    if any(t in text for t in _FRAGRANCE_TERMS):
        return "fragrance"
    if any(t in text for t in _MAKEUP_SKINCARE_TERMS):
        return "makeup_skincare"
    return None
