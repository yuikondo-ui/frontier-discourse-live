# Frontier AI discourse: live map updater

This repo keeps a Latent Lab map of AI discourse up to date. The map has three layers: frontier AI company blogs and newsrooms, news outlets, and government policy.

- `main` branch: the code (this file and `scripts/`).
- `claude/live-data` branch: the data Latent Lab syncs from. It holds one markdown file per item at `items/<layer>/<YYYY-MM>/<id>.md`. The body is a 35–50 word topic description with names masked, and the metadata sits in YAML front matter. It was seeded on 2026-10-14 with the 1,467 items of Latent Lab map #713 (2025-09-25 to 2026-09-25).

Only edit files under `items/` on the data branch. Every file there becomes a point on the map.

## Daily update (what the scheduled routine does)

1. Check out the data branch next to this repo:
   ```bash
   git fetch origin claude/live-data
   git worktree add ../data claude/live-data
   ```
2. Install dependencies, then collect documents published in the last 3 days that are not on the map yet:
   ```bash
   pip install -q requests trafilatura
   python3 scripts/live_collect.py --data ../data --days 3
   ```
   This writes `work/to_code.jsonl`. `GUARDIAN_API_KEY` and `DATA_GOV_API_KEY` are optional environment variables; without them, The Guardian and Congress are skipped. If no new documents turn up, stop here and report that.
3. Code every line of `work/to_code.jsonl` following `scripts/live_prompt.md` and `scripts/harmonize_prompt.md`, and write `work/coded.jsonl`. Read each document's text; do not code from the title. Work in chunks of about 10 and append as you go.
4. Validate, fix and publish:
   ```bash
   python3 scripts/live_publish.py --data ../data --check   # repeat until ok
   python3 scripts/live_publish.py --data ../data
   ```
5. Commit and push the data branch only:
   ```bash
   cd ../data && git add items && git commit -m "live update $(date -u +%F): N items" && git push origin claude/live-data
   ```
6. Report the number of new items per layer, the sources that failed (from the collector log), and any items you dropped.

Never commit `work/` and never push to `main`. Latent Lab picks up the push within about 5 minutes.
