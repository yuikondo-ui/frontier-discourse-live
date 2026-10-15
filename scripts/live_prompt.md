# Live coding prompt (company + news + policy layers)

Each line of `work/to_code.jsonl` is one document:
`{id, layer, source, channel, jurisdiction, title, url, date, text}`.
`layer` is "company" (a frontier AI company's own blog or newsroom), "news" (a news outlet) or "policy" (a government document).

Write `work/coded.jsonl` with exactly one JSON line per input id, and nothing else:

```json
{"id": "...", "ai_relevant": "yes", "topic_desc": "...", "topic_tags": ["...", "..."],
 "frame": "capability", "focal_company": "OpenAI", "main_voice": "company", "doc_type": "news_report",
 "vet_valence": 0, "vet_effectiveness": 1, "vet_trajectory": 0}
```

## Fields

- `ai_relevant`: "yes" if AI is the main subject, "partial" if it is one of several subjects, "no" if AI is absent or incidental. Items marked "no" are not published, but they still need a line.
- `topic_desc`, `topic_tags`: follow **scripts/harmonize_prompt.md** exactly. This is the text that gets embedded, so it is the most important part. It must be 35–50 words (20–35 for very thin material), use the fixed structure, contain no genre or stance words, mask names, and use the canonical vocabulary. Write it from the document text, not from the title alone.
- `frame`: exactly one of "capability", "benefit", "risk", "governance", "economy", "science".
- `focal_company`: the single company most central to the document, chosen from "OpenAI", "Anthropic", "Google", "Meta", "Microsoft", "Amazon", "xAI", "NVIDIA", "Other". Use "" if none applies. For the company layer it is normally the publishing company.
- `main_voice` (news and policy only; use "" for company): whose claims carry the document. One of "company", "officials", "experts", "critics", "journalist", "mixed".
- `doc_type` (news and policy only; use "" for company):
  - news: one of "news_report", "analysis_feature", "opinion", "interview", "product_review"
  - policy: one of "rule_or_notice", "bill", "hearing", "executive_action", "press_release", "guidance_or_standard", "speech_or_statement", "other"
- VET is the document's own stance. Each score is an integer from −2 to +2, and 0 means neutral or absent.
  - `vet_valence`: −2 foregrounds serious harm … +2 enthusiastic transformative good
  - `vet_effectiveness`: −2 AI limited, unreliable or overhyped … +2 very powerful
  - `vet_trajectory`: −2 slow or incremental … +2 rapid, accelerating or imminent

## Rules

Base everything only on the provided text. Never mention clipping. Keep hazardous technical detail (exploits, weapons, self-harm methods) at a high level.
When you are done, run `python3 scripts/live_publish.py --data <data checkout> --check`. Then fix every violation it reports by rewriting the description; do not just delete words. Repeat until the check passes.
