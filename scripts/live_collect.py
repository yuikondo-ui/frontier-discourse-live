#!/usr/bin/env python3
"""Live collection: find company / news / policy documents published in the last few days
that are not on the map yet, and write them to work/to_code.jsonl for coding.

Reuses the discovery, fetch and extract code of collect_year.py (companies) and
collect_layers.py (news + policy) unchanged; only the candidate selection differs:
instead of sampling a whole month, it keeps the newest candidates per channel.

Usage:
    python3 scripts/live_collect.py --data ../data            # data = checkout of the data branch
    python3 scripts/live_collect.py --data ../data --days 7   # longer look-back (e.g. after a gap)
Keys (optional, environment): GUARDIAN_API_KEY, DATA_GOV_API_KEY, FIRECRAWL_API_KEY.
Missing keys skip those sources; without Firecrawl, pages that block plain fetch (e.g. OpenAI) are skipped.
"""
import argparse, json, os, sys, time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_year as cy  # noqa: E402
import collect_layers as cl  # noqa: E402
import scrape as sc  # noqa: E402

WORK = ROOT / "work"
PER_CHANNEL = 12   # newest candidates fetched per channel / source per run
CLIP_WORDS = 3500  # same clip as the phase-4 coding
FIRECRAWL_MAX = 25  # Firecrawl pages per run (credits)


def published_ids(data):
    return {p.stem for p in Path(data).glob("items/**/*.md")}


def front_date(path):
    for line in open(path, encoding="utf-8"):
        if line.startswith("date:"):
            return line.split(":", 1)[1].strip().strip('"')[:10]
    return ""


def newest(rows, group, best_date, start, k):
    by = defaultdict(list)
    for r in rows:
        d = best_date(r)
        if d and d[:10] >= start.isoformat():
            by[group(r)].append((d, r))
    out = []
    for g, rs in by.items():
        out += [r for _, r in sorted(rs, key=lambda x: x[0], reverse=True)[:k]]
    return out


def firecrawl(rows, start, end):
    """Pages plain fetch could not read: scrape with Firecrawl if a key is set.
    rows: dicts with url, known date (or ""), and the output fields for the doc."""
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not rows:
        return []
    if not key:
        print(f"[firecrawl] {len(rows)} pages need Firecrawl; FIRECRAWL_API_KEY not set, skipped")
        return []
    out = []
    for r in rows[:FIRECRAWL_MAX]:
        try:
            text, meta, title = sc.scrape_firecrawl(r["url"], key)
        except Exception as e:
            print(f"  firecrawl failed {r['url']}: {e}"); continue
        d = sc.pick_date(meta)[0] or r.pop("known_date", "") or ""
        r.pop("known_date", None)
        words = len(text.split())
        if not d or not (start.isoformat() <= d <= end.isoformat()) or words < 120:
            continue
        if r["layer"] != "company" and len(cy.AI_TERMS.findall(text)) < 3:
            continue
        out.append({**r, "title": title or r["title"], "date": d, "text": text})
        time.sleep(7)  # Firecrawl rate limit (see PROCESS_LOG audit finding 3)
    print(f"[firecrawl] {len(out)} of {min(len(rows), FIRECRAWL_MAX)} scraped pages kept")
    return out


def collect_companies(start, end):
    base = WORK / "company"
    P = dict(base=base, cand=base / "phase1" / "candidates.csv", inv=base / "phase1" / "url_inventory.csv",
             fc=base / "phase1" / "firecrawl_todo.csv", html=base / "html", raw=base / "phase2" / "raw",
             jsonl=base / "phase2" / "corpus_raw.jsonl")
    for d in (base / "phase1", P["html"], P["raw"]):
        d.mkdir(parents=True, exist_ok=True)
    cy.CH[:] = [c for c in cy.CH if c["company"] != "NVIDIA"]  # the map's company layer is the 6 FMF members
    rows = cy.discover(SimpleNamespace(root="work/company"), start, end, P)

    def best(r):  # feed date or full URL date; lastmod only as a fallback upper bound
        ud = r["url_date"] if r["url_date"] and not r["url_date"].endswith("-01") else ""
        return r["feed_date"] or ud or r["lastmod"]
    rows = newest(rows, lambda r: (r["company"], r["channel"]), best, start, PER_CHANNEL)
    print(f"[company] {len(rows)} recent candidates")
    fc, inv = [], []
    if rows:
        cy.fetch_all(rows, P, 1.5)
        inv, fc = cy.date_and_extract(rows, start, end, P)
    pub = {r["url"]: r["published_date"] for r in inv}  # the date the extractor settled on
    out = firecrawl([dict(id=cy.did(r["url"]), layer="company", source=r["company"], channel=r["channel"],
                          jurisdiction="", title=r["title"], url=r["url"], known_date=r["published_date"])
                     for r in fc], start, end)
    if P["jsonl"].exists():
        for line in open(P["jsonl"], encoding="utf-8"):
            d = json.loads(line)
            out.append(dict(id=d["doc_id"], layer="company", source=d["company"], channel=d["channel"],
                            jurisdiction="", title=d["title"], url=d["url"],
                            date=(pub.get(d["url"]) or d.get("date_meta") or d.get("date_inventory") or "")[:10],
                            text=d["text"]))
    return out


def collect_layers(start, end):
    base = WORK / "layers"
    P = dict(cand=base / "phase1" / "candidates.csv", samp=base / "phase1" / "sampled.csv", html=base / "html",
             raw=base / "phase2" / "raw", jsonl=base / "phase2" / "corpus_raw.jsonl",
             capped=base / "phase2" / "corpus_capped.jsonl", fc=base / "phase1" / "firecrawl_todo.csv")
    for d in (base / "phase1", P["html"], P["raw"]):
        d.mkdir(parents=True, exist_ok=True)
    rows = cl.discover(["news", "policy"], start, end, P)
    rows = newest(rows, lambda r: r["source"], lambda r: r.get("date_guess") or "", start, PER_CHANNEL)
    print(f"[layers] {len(rows)} recent candidates")
    fc = []
    if rows:
        cy.fetch_all([r for r in rows if r["kind"] == "sitemap"], dict(html=P["html"]), 1.5)
        cl.fetch_text_urls([r for r in rows if r["kind"] in ("fedreg", "govinfo")], P)
        _, fc = cl.extract_all(rows, start, end, P)
    out = firecrawl([dict(id=r["doc_id"], layer=r["layer"], source=r["source"], channel=r["cell"],
                          jurisdiction=r.get("jurisdiction", ""), title=r.get("title", ""), url=r["url"],
                          known_date=(r.get("date_guess") or "")[:10]) for r in fc], start, end)
    if P["jsonl"].exists():
        for line in open(P["jsonl"], encoding="utf-8"):
            d = json.loads(line)
            out.append(dict(id=d["doc_id"], layer=d["layer"], source=d["source"], channel=d["cell"],
                            jurisdiction=d.get("jurisdiction", ""), title=d["title"], url=d["url"],
                            date=d["date"], text=d["text"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="checkout of the data branch (items/ lives there)")
    ap.add_argument("--days", type=int, default=0,
                    help="look-back in days; default: since the newest item on the map (3 to 21 days)")
    ap.add_argument("--end", default=date.today().isoformat())
    a = ap.parse_args()
    end = date.fromisoformat(a.end)
    have = published_ids(a.data)
    days = a.days
    if not days:
        months = sorted(p.name for p in Path(a.data).glob("items/*/*") if p.is_dir())
        newest_day = max((d for m in months[-1:] for f in Path(a.data).glob(f"items/*/{m}/*.md")
                          for d in [front_date(f)] if d), default="")
        gap = (end - date.fromisoformat(newest_day)).days + 1 if newest_day else 3
        days = min(max(gap, 3), 21)
    start = end - timedelta(days=days)
    WORK.mkdir(exist_ok=True)
    print(f"window {start} .. {end}; {len(have)} items already on the map")

    docs = []
    for name, fn in (("company", collect_companies), ("news+policy", collect_layers)):
        try:
            docs += fn(start, end)
        except Exception as e:  # one broken collector must not stop the other
            print(f"! {name} collector failed: {type(e).__name__}: {e}")

    seen, new = set(have), []
    for d in docs:
        if d["id"] in seen or not d["date"] or not (start.isoformat() <= d["date"] <= end.isoformat()):
            continue
        seen.add(d["id"])
        d["text"] = " ".join(d["text"].split()[:CLIP_WORDS])
        new.append(d)
    with open(WORK / "to_code.jsonl", "w", encoding="utf-8") as f:
        for d in new:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    by = defaultdict(int)
    for d in new:
        by[d["layer"]] += 1
    print(f"[live] {len(new)} new documents -> work/to_code.jsonl  {dict(by)}")


if __name__ == "__main__":
    main()
