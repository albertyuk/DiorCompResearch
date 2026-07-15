# Consolidation — cluster kept posts into projects (campaign AND phase)

You receive the month's kept Weibo posts for ONE brand. Cluster them into projects.

**Cluster key = campaign AND phase.** The reference decks split one campaign into
separate rows per phase: TEASER, CELEBS, EVENT, MAKING OF, VIP ARRIVAL, SCENOGRAPHY,
ARRIVAL. Posts about the same campaign but different phases are DIFFERENT projects.
Posts of the same campaign+phase within a rolling ≤10-day window form ONE project.

Examples of distinct projects from real decks:
- "LA GALERIE DU 19M SHANGHAI — TEASER" vs "LA GALERIE DU 19M SHANGHAI — MAKING OF"
- "LOUIS VUITTON SS27 PARIS MENS FASHION SHOW — TEASER" vs same show "— VIP ARRIVAL"
- A month-long product seeding push (many celeb posts, same bag) = ONE project
  (e.g. "FENDI BAGUETTE", ongoing).

## Brand

{{brand_display}}

## Posts (JSON)

{{posts_json}}

## Output — strict JSON only

{
  "projects": [
    {
      "title": "CANONICAL PROJECT TITLE IN CAPS, NO PHASE SUFFIX",
      "phase_suffix": "TEASER" | "CELEBS" | "EVENT" | "MAKING OF" | "VIP ARRIVAL" | "SCENOGRAPHY" | "ARRIVAL" | null,
      "post_ids": ["..", ".."],
      "category": "campaign|event|cover|product|show|other",
      "ongoing": true or false
    }
  ]
}

Notes:
- Every input post_id must appear in exactly one project.
- `ongoing`: true when the activity clearly continues past month end (e.g. a seeding
  campaign still active at the last day of the month).
- Titles: English, ALL CAPS, ≤60 chars, matching the register of the examples
  ("COCO CRUSH FINE JEWELLERY HANGZHOU POP-UP", "GUCCI MONTE CARLO").
