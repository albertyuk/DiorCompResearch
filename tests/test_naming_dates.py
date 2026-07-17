from datetime import date

from mm import naming
from mm.dates import date_runs, date_text, previous_month


# -- dates ----------------------------------------------------------------------

def test_single_day_superscript():
    assert date_runs(date(2026, 7, 9), None) == [("JULY 9", False), ("TH", True)]


def test_range_en_dash():
    assert date_text(date(2026, 7, 2), date(2026, 7, 3)) == "JULY 2ND – 3RD"


def test_ongoing_tbd():
    assert date_text(date(2026, 7, 1), None, ongoing=True) == "JULY 1ST – TBD"


def test_ordinal_edge_cases():
    assert date_text(date(2026, 7, 1), None) == "JULY 1ST"
    assert date_text(date(2026, 7, 11), None) == "JULY 11TH"
    assert date_text(date(2026, 7, 12), None) == "JULY 12TH"
    assert date_text(date(2026, 7, 13), None) == "JULY 13TH"
    assert date_text(date(2026, 7, 21), None) == "JULY 21ST"
    assert date_text(date(2026, 7, 22), None) == "JULY 22ND"
    assert date_text(date(2026, 7, 23), None) == "JULY 23RD"


def test_cross_month_range():
    assert date_text(date(2026, 6, 28), date(2026, 7, 3)) == "JUNE 28TH – JULY 3RD"


def test_previous_month():
    assert previous_month(date(2026, 8, 15)) == "2026-07"
    assert previous_month(date(2026, 1, 2)) == "2025-12"


# -- naming ---------------------------------------------------------------------

def test_phase_suffix_standardization():
    assert naming.split_phase_suffix("LA GALERIE DU 19M SHANGHAI -- TEASER") == \
        ("LA GALERIE DU 19M SHANGHAI", "TEASER")
    assert naming.split_phase_suffix("SHOW – MAKING OF") == ("SHOW", "MAKING OF")
    assert naming.split_phase_suffix("SHOW -ARRIVAL") == ("SHOW", "ARRIVAL")
    assert naming.split_phase_suffix("FENDI BAGUETTE") == ("FENDI BAGUETTE", None)
    assert naming.format_title("show", "vip arrival") == "SHOW — VIP ARRIVAL"


def test_caps_pinyin():
    assert naming.caps_pinyin("王一博") == "WANG YIBO"
    assert naming.caps_pinyin("唐嫣") == "TANG YAN"


def test_display_name_prefers_english():
    assert naming.display_name("Leah Dou", "窦靖童") == "LEAH DOU"
    assert naming.display_name(None, "宋威龙") == "SONG WEILONG"


def test_relation_display_question_mark():
    assert naming.relation_display("BRAND AMBASSADOR", None, True) == "BRAND AMBASSADOR"
    assert naming.relation_display(None, "actress", False) == "ACTRESS ?"
    assert naming.relation_display(None, None, False) == "CELEBRITY ?"


def test_social_column_fixed_order():
    assert naming.social_column(["wechat_mp", "weibo", "xhs"]) == "WEIBO/RED/WECHAT"
    assert naming.social_column(["douyin", "weibo"]) == "WEIBO/DOUYIN"
    assert naming.social_column(["weibo", "xhs", "wechat_channels", "douyin"]) == \
        "WEIBO/RED/WECHAT/DOUYIN"


def test_assets_label():
    assert naming.assets_label(True, False) == "PHOTO"
    assert naming.assets_label(False, True) == "VIDEO"
    assert naming.assets_label(True, True) == "PHOTO VIDEO"


def test_date_runs_none_start_is_tbd():
    assert date_text(None, None) == "TBD"
    assert date_text(None, None, ongoing=True) == "TBD"


# -- the site clock is Beijing time -------------------------------------------

def test_now_iso_is_beijing_time():
    """Owner report: timestamps ran on the server clock (UTC on Fly). Every
    generated timestamp is Beijing wall-clock time with an explicit +08:00."""
    from datetime import datetime
    from mm import db
    from mm.dates import CST
    stamp = db.now_iso()
    assert stamp.endswith("+08:00")
    parsed = datetime.fromisoformat(stamp)
    beijing_now = datetime.now(CST)
    assert abs((beijing_now - parsed).total_seconds()) < 5
    assert parsed.hour == beijing_now.hour       # wall clock, not UTC digits
