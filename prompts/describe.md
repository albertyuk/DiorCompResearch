# Project line — one ≤90-character English line for the deck's PROJECT cell

Write the one-line project description used in the report table. Match the register
of these real examples exactly (ALL CAPS, brand voice, celeb names in caps pinyin
or known English stage names):

- GUCCI MONTE CARLO WITH TIAN XIWEI
- BAZAAR MEN CHINA'S JUNE 2026 ISSUE WITH SONG WEILONG
- COCO CRUSH FINE JEWELLERY AD WITH WANG YIBO AND LEAH DOU
- LI YUNRUI VISITS OAK SPRING GARDEN, TRACING SCHLUMBERGER'S LEGACY
- TIFFANY NEW CAMPAIGN FACE WITH SAMMI CHENG, TITLE TBD

## Project

Brand: {{brand_display}}
Working title: {{title}}
Phase: {{phase_suffix}}
Category: {{category}}
Celebs (name + relation): {{celebs}}
Representative captions:
{{captions}}

## Output — strict JSON only

{
  "description": "THE LINE IN CAPS, <= 90 CHARS, NO TRAILING PERIOD"
}

Notes:
- Include "WITH <CELEB>" when 1–2 celebs anchor the project; omit the celeb list
  when there are 3+ (the title alone carries it).
- Never invent facts not present in the captions.
