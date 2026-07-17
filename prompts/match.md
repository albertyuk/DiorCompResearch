# Same-event judgment — is this candidate post the same project as the Weibo reference?

A "project" is ONE specific campaign moment: a product drop, event, show,
pop-up, collaboration or announcement, usually echoed across Chinese social
platforms within a few days.

## Reference (Weibo, already confirmed relevant)

Brand: {{brand_display}}
Date: {{ref_date}}
Caption:
{{ref_caption}}

Hashtags: {{ref_hashtags}}
Celebs involved: {{ref_celebs}}
Project working title: {{ref_title}}

## Candidate (from {{candidate_platform}})

Date: {{candidate_date}}
Title/Caption:
{{candidate_caption}}

Hashtags: {{candidate_hashtags}}
@-tags: {{candidate_at_tags}}

## Why this pair was queued (heuristic evidence — verify it, don't trust it)

{{heuristic_evidence}}

## Task

`same_event` is true ONLY if both posts cover the SAME specific campaign
moment — the same product/collection name, the same event/venue/city, the
same show or announcement — within a few days of each other.

It is NOT enough that both posts:
- come from the same brand (they always do here);
- feature the same ambassador or celebrity — ambassadors appear across many
  different campaigns in the same window;
- use the same generic marketing language (新品, 优雅, 演绎, 魅力, …);
- mention the same broad product category (handbags, jewellery, watches).

When the captions are too thin to identify a concrete shared campaign,
answer false. A wrong tick puts a false platform claim in a client-facing
deck; a miss is recoverable by the human reviewer from the orphan list.

Confidence calibration:
- 0.9+ — an explicit shared campaign/collection/event name or hashtag
- 0.7–0.85 — strong circumstantial evidence (same unique product + same
  venue/city + close dates)
- below 0.7 — do not match; if unsure, same_event = false

## Output — strict JSON only

{
  "same_event": true or false,
  "confidence": 0.0 to 1.0,
  "reason": "one short sentence naming the concrete shared campaign moment (or why there is none)"
}
