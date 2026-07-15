# Same-event judgment — is this candidate post the same project as the Weibo reference?

A "project" is one brand campaign/event/editorial moment, possibly posted across
several Chinese social platforms within a few days.

## Reference (Weibo, already confirmed relevant)

Brand: {{brand_display}}
Date: {{ref_date}}
Caption:
{{ref_caption}}

Celebs involved: {{ref_celebs}}
Project working title: {{ref_title}}

## Candidate (from {{candidate_platform}})

Date: {{candidate_date}}
Title/Caption:
{{candidate_caption}}

## Task

Judge whether the candidate covers the SAME project (same campaign AND same phase —
a teaser and the event itself are different phases but count as the same event here
only if the candidate clearly covers the same campaign moment within ±5 days).

Signals: shared celebrity names, shared campaign/collection names (COCO CRUSH,
BAGUETTE, 19M…), shared venue/city, same product line, close dates.

## Output — strict JSON only

{
  "same_event": true or false,
  "confidence": 0.0 to 1.0,
  "reason": "one short sentence"
}
