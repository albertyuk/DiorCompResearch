# Caption relation extraction — celebrity titles stated in the caption

Chinese luxury captions usually state the celebrity's official title right next to
the @ mention (e.g. "品牌挚友@朱一龙", "CHANEL品牌大使@王一博", "全球代言人@刘雨昕").
Extract, for each tagged celebrity, the EXACT title as written — never infer one
that is not literally in the text.

Title mapping (apply, but also return the raw Chinese):
- 全球代言人 / 品牌代言人 / 代言人 / 品牌大使 / 形象大使 / 大使 → BRAND AMBASSADOR
- 品牌挚友 / 品牌好友 / 挚友 → BRAND FRIEND
- Anything else or no title stated → null

Also extract the celebrity's occupation ONLY if stated in the caption
(演员=ACTRESS/ACTOR by context, 歌手=SINGER, 艺术家=ARTIST, 钢琴家=PIANIST,
导演=DIRECTOR). Do not guess occupations from your own knowledge here.

## Caption

Brand: {{brand_display}}
Caption (原文):
{{caption}}

Tagged handles: {{at_tags}}

## Output — strict JSON only

{
  "celebs": [
    {
      "handle": "@..",
      "name_cn": "中文名 or the handle text without @",
      "raw_cn_title": "exact Chinese title text or null",
      "relation": "BRAND AMBASSADOR" | "BRAND FRIEND" | null,
      "occupation": "ACTRESS" | "ACTOR" | "SINGER" | "ARTIST" | "PIANIST" | "DIRECTOR" | null
    }
  ]
}

Notes:
- A title applies only to the @ it is attached to, not to every celeb in the caption.
- NEVER invent a relation. If the caption doesn't state one, relation = null.
