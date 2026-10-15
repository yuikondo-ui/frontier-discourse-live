# Description harmonization (topic-only)

Items come from four very different genres: corporate blog posts, news articles, government documents (bills, notices, hearings, press releases) and short social-media posts. They will be embedded together. Rewrite each item into ONE standardized description so that items cluster by **topic**, not by genre, register, length or stance.

For each input line `{id, material}` output one JSON line:

```json
{"id": "...", "topic_desc": "...", "topic_tags": ["...", "...", "..."]}
```

## topic_desc rules

1. **Length: 35–50 words**, one paragraph, 2–3 sentences. If the material is a short post with little content, write 20–35 words. Never invent details that are not in the material.
2. **Fixed structure, in this order:**
   - Sentence 1: the subject. Which AI technology, system, or issue (e.g. "Coding agents that run autonomously in software repositories", "Electricity demand from large AI data centers").
   - Sentence 2: the development or claim. What happened, was proposed, found, or argued.
   - Optional sentence 3: who or what domain is involved or affected, in generic terms (developers, schoolchildren, hospitals, utilities, national security).
3. **Present tense, plain neutral third person.** Start directly with the subject noun phrase.
4. **No genre or format markers.** Never write: post, article, blog, tweet, thread, bill, act, legislation (as the document type), notice, hearing, testimony, press release, announcement, newsletter, op-ed, column, review, interview, podcast, episode, report (as the document), "the author", "the writer", "the company says", "the government", "this document", "readers", "users of the platform", hashtags, links, @handles, emoji.
   - Describe the *substance* instead. Write "A proposed federal requirement that…" only if the rule itself is the topic. Write "Rules requiring…" rather than "The bill would require…".
5. **No stance or tone.** Drop hype and alarm words: revolutionary, groundbreaking, exciting, dangerous, terrifying, slop, game-changing. Stance is coded elsewhere. Report the claim neutrally: "Claims that… / Findings that…".
6. **Masking.** Replace companies with [COMPANY], other organisations (agencies, universities, NGOs, courts, legislatures) with [PARTNER], named models and products with [MODEL] / [PRODUCT], and people with [PERSON]. Keep generic technology nouns (large language model, chatbot, coding agent, image generator, GPU, data center) and countries and regions.
7. **Same vocabulary for the same thing.** Prefer these canonical terms: "large language models", "AI agents", "coding agents", "AI chips", "AI data centers", "AI safety", "AI regulation", "frontier AI models", "generative images/video", "chatbots", "AI in education", "AI in healthcare", "AI and jobs", "AI investment", "AI security / cyberattacks", "children's online safety", "copyright and training data", "military AI".

## topic_tags

Give 3–5 lowercase tags of 1–3 words each, naming topics only. Use canonical terms where they fit, for example: "coding agents", "ai data centers", "copyright". Use no company, genre or stance words.

## Process

- Read every item. Work in a few chunks and save progress as you go.
- After writing, validate with Python:
  - one line per input id;
  - word counts between 20 and 55;
  - no banned genre words, checked case-insensitively on whole words: post, article, blog, tweet, thread, bill, notice, hearing, testimony, press release, announcement, newsletter, op-ed, column, podcast, episode, interview, the author, this document;
  - no company, product or person names. Check at least OpenAI, Anthropic, Google, DeepMind, Meta, Microsoft, Amazon, AWS, NVIDIA, xAI, ChatGPT, Claude, Gemini, Copilot, Grok, Llama, Sora, Alexa, Musk, Altman, Trump, Newsom, White House, Pentagon, Congress, Commission, plus any capitalised proper nouns you notice;
  - 3–5 tags per item.
- Fix every violation by rewriting the description. Do not just delete words.
- Hazardous material (weapons, exploits, self-harm methods) stays at a high level.
