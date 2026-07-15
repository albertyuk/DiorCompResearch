# Relevance filter — one Weibo post from a luxury maison's official account

You are screening posts for Dior China's fashion PR competitive-intelligence report.
Decide whether this post is China-market-focused brand activity that a Dior China
fashion PR team would consider a comparable move.

## Post

Brand: {{brand_display}}
Platform: Weibo (official account {{account_name}})
Posted at: {{created_at}}
Caption (原文):
{{caption}}

@-tags: {{at_tags}}
Hashtags: {{hashtags}}
Media: {{media_summary}}

## Rubric

**KEEP** if the post is China-market-focused brand activity a Dior China fashion PR
team would consider a comparable move:
- CN celebrity collaborations or appearances
- CN events, pop-ups, exhibitions
- Shows and show teasers
- CN magazine covers/editorials
- CNY & local festival capsules
- CN store or EC launches
- Livestream announcements
- CN-relevant CSR with celebrity involvement

Chinese-celebrity @-tags are a **strong positive signal but not required** — a
China-exclusive pop-up with no celeb still KEEPs.

**HARD EXCLUDE:**
- Makeup/skincare content (美妆/彩妆/护肤). Fragrance (香水/香氛) stays IN —
  it is NOT excluded.
- Pure global campaign reposts with no China angle.

## Output — strict JSON only

{
  "keep": true or false,
  "confidence": 0.0 to 1.0,
  "reasons": ["short reason", ".."],
  "celebs_tagged": [
    {"handle": "@..", "name_cn": "中文名", "name_en_guess": "PINYIN OR KNOWN ENGLISH NAME"}
  ],
  "category": "campaign" | "event" | "cover" | "product" | "show" | "other",
  "media_focus": "photo" | "video" | "both"
}

Notes:
- Bias to recall: if you are unsure, lean KEEP with a lower confidence.
- `celebs_tagged` lists PEOPLE only (actors, singers, artists, athletes, directors,
  pianists…), not brand sub-accounts or magazines.
- `name_en_guess`: the celebrity's commonly used English/stage name if you know it
  (e.g. 王一博 → "WANG YIBO", 窦靖童 → "LEAH DOU"), else caps pinyin.
