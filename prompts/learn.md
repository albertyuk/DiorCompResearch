# Filter self-tuning — distill human corrections into prompt guidance

The relevance filter below screens Weibo posts for a China-market
competitive-intelligence report. Humans review its keep/drop verdicts in a
console; every override they make is logged. Your job: study the corrections
and produce a SHORT list of guidance rules that would have prevented the
mistakes — so the filter gets more accurate over time.

## The filter's core rubric (fixed — you may NOT contradict it)

- Core test: China-market-specific → KEEP; global campaign, no China angle → DROP.
- Hard excludes (never soften): perfume/fragrance, makeup/skincare.
- Strong keep signals: Chinese celebrities (especially @-tagged), specific
  locations in China, CN festivals/exclusives.

## Current learned guidance (replace, don't append)

{{current_rules}}

## Human corrections since (each: what the model said → what the human decided)

{{feedback}}

## Instructions

- Look for SYSTEMATIC patterns, not one-offs: categories of posts the model
  keeps but humans drop (or vice versa), signals it over/under-weighs,
  recurring celebrity names or event types it misjudges.
- Write at most 12 rules, each one line, imperative, concrete
  ("Posts that only announce a global runway livestream with no CN celebrity
  or CN platform → DROP", not "be more careful with shows").
- Carry forward still-valid rules from the current guidance; drop ones the
  corrections no longer support. The output REPLACES the current guidance.
- If corrections are contradictory or too few to generalize, output fewer
  rules — an empty list is acceptable. Never invent a rule that no
  correction supports.
- Rules must never contradict the fixed rubric above.

## Output — strict JSON only

{
  "rules_md": "- rule one\n- rule two\n…  (markdown bullets, may be empty string)",
  "summary": "one line describing what changed and why"
}
