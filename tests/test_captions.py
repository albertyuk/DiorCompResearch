"""Caption fixtures (~15 realistic Weibo captions): @-tag extraction, Chinese
title → relation mapping, beauty exclusion policy (perfume AND makeup/skincare
are both policy DROP — owner directive 2026-07)."""
import json
from pathlib import Path

import pytest

from mm import naming
from mm.normalize import _AT_RE

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures_captions.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("fx", FIXTURES, ids=[f"c{f['id']}-{f['note'][:28]}" for f in FIXTURES])
def test_at_tag_extraction(fx):
    tags = _AT_RE.findall(fx["caption"])
    assert tags == fx["expect_at_tags"]


@pytest.mark.parametrize("fx", [f for f in FIXTURES if f["expect_title_map"]],
                         ids=[f"c{f['id']}" for f in FIXTURES if f["expect_title_map"]])
def test_cn_title_mapping(fx):
    exp = fx["expect_title_map"]
    assert exp["raw"] in fx["caption"]
    assert naming.map_cn_title(exp["raw"]) == exp["relation"]
    assert naming.map_cn_title(fx["caption"]) == exp["relation"]


@pytest.mark.parametrize("fx", FIXTURES, ids=[f"c{f['id']}" for f in FIXTURES])
def test_cosmetics_signal(fx):
    assert naming.cosmetics_signal(fx["caption"]) == fx["expect_cosmetics_signal"]


def test_perfume_and_beauty_policy_is_drop():
    # owner directive 2026-07: anything perfume related is dropped, same as
    # makeup/skincare — even with a Chinese celebrity attached
    for fx in FIXTURES:
        if fx["expect_cosmetics_signal"] in ("fragrance", "makeup_skincare"):
            assert fx["expect_keep_policy"] is False, fx["id"]


def test_no_title_maps_to_none():
    assert naming.map_cn_title("现身巴黎大秀现场") is None
    assert naming.map_cn_title("") is None
