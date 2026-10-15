#!/usr/bin/env python3
"""Phase 2: scrape article pages listed in phase1/url_inventory.csv.

Usage (from the project folder):
    pip install requests trafilatura python-dotenv
    python scripts/scrape.py                 # Firecrawl if FIRECRAWL_API_KEY is set, else trafilatura
    python scripts/scrape.py --engine trafilatura

Reads  : phase1/url_inventory.csv  (rows with include_v1 in {Y, review})
Writes : phase2/raw/<doc_id>.md    (main-content markdown/text)
         phase2/corpus_raw.jsonl   (one JSON per doc, metadata + text)
         phase2/scrape_log.csv     (status per URL)
Re-running skips docs that already have a raw file.
"""
import argparse, csv, hashlib, json, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
INV = ROOT / "phase1" / "url_inventory.csv"
OUT = ROOT / "phase2"
RAW = OUT / "raw"

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "firecrawl.env")
except ImportError:
    for f in (ROOT / ".env", ROOT / "firecrawl.env"):
        if f.exists():
            for line in f.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

UA = "Mozilla/5.0 (research crawler; MIT Media Lab Viral Communications; contact: yui.kondo@and-e.com)"
DATE_KEYS = ["article:published_time", "publishedTime", "datePublished", "og:published_time",
             "parsely-pub-date", "date", "dc.date", "pubdate"]


def doc_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def pick_date(meta: dict):
    for k in DATE_KEYS:
        for mk, v in meta.items():
            if mk.lower() == k.lower() and v:
                v = v[0] if isinstance(v, list) else v
                m = re.search(r"\d{4}-\d{2}-\d{2}", str(v))
                if m:
                    return m.group(0), mk
    return None, None


def scrape_firecrawl(url, key, max_tries=5):
    """Firecrawl with backoff on 429 (rate limit) and 5xx."""
    for attempt in range(1, max_tries + 1):
        r = requests.post("https://api.firecrawl.dev/v2/scrape",
                          headers={"Authorization": f"Bearer {key}"},
                          json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
                          timeout=120)
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After", 0) or 0) or 15 * attempt
            print(f"    firecrawl {r.status_code}; waiting {wait}s (try {attempt}/{max_tries})")
            time.sleep(wait)
            continue
        r.raise_for_status()
        data = r.json().get("data", {})
        meta = data.get("metadata", {}) or {}
        return data.get("markdown", ""), meta, meta.get("title") or meta.get("ogTitle")
    raise RuntimeError(f"firecrawl still failing after {max_tries} tries (last {r.status_code})")


def scrape_trafilatura(url):
    import trafilatura
    html = requests.get(url, headers={"User-Agent": UA}, timeout=60).text
    text = trafilatura.extract(html, output_format="markdown", include_links=False,
                               include_comments=False, favor_precision=True) or ""
    md = trafilatura.extract_metadata(html)
    meta = {}
    if md:
        meta = {"title": md.title, "datePublished": md.date, "author": md.author}
    for prop in ["article:published_time", "og:published_time"]:
        m = re.search(rf'<meta[^>]+(?:property|name)="{prop}"[^>]+content="([^"]+)"', html)
        if m:
            meta[prop] = m.group(1)
    m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    if m:
        meta.setdefault("datePublished", m.group(1))
    return text, meta, meta.get("title")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["auto", "firecrawl", "trafilatura"], default="auto")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=7.0)  # ~8-9 req/min, under free-tier limits
    ap.add_argument("--inv", default=None, help="inventory CSV (default phase1/url_inventory.csv)")
    ap.add_argument("--out", default=None, help="output dir (default phase2/)")
    a = ap.parse_args()
    global INV, OUT, RAW
    if a.inv: INV = ROOT / a.inv
    if a.out: OUT = ROOT / a.out; RAW = OUT / "raw"

    key = os.getenv("FIRECRAWL_API_KEY", "")
    engine = a.engine if a.engine != "auto" else ("firecrawl" if key else "trafilatura")
    RAW.mkdir(parents=True, exist_ok=True)

    rows = [r for r in csv.DictReader(open(INV, encoding="utf-8")) if r["include_v1"] in ("Y", "review")]
    if a.limit:
        rows = rows[: a.limit]
    print(f"{len(rows)} URLs, engine={engine}")

    jl = open(OUT / "corpus_raw.jsonl", "a", encoding="utf-8")
    log_path = OUT / "scrape_log.csv"
    new_log = not log_path.exists()
    log = csv.writer(open(log_path, "a", newline="", encoding="utf-8"))
    if new_log:
        log.writerow(["doc_id", "url", "status", "chars", "date_meta", "date_key", "engine", "ts"])

    for i, r in enumerate(rows, 1):
        did = doc_id(r["url"])
        if (RAW / f"{did}.md").exists():
            continue
        try:
            used = engine
            if engine == "firecrawl":
                try:
                    text, meta, title = scrape_firecrawl(r["url"], key)
                except Exception as fe:
                    print(f"    firecrawl failed ({fe}); falling back to trafilatura")
                    text, meta, title = scrape_trafilatura(r["url"]); used = "trafilatura"
            else:
                text, meta, title = scrape_trafilatura(r["url"])
            if len(text.split()) < 50:
                raise RuntimeError(f"too little text ({len(text.split())} words)")
            date_meta, date_key = pick_date(meta)
            (RAW / f"{did}.md").write_text(text, encoding="utf-8")
            rec = {
                "doc_id": did, "company": r["company"], "channel": r["channel"],
                "genre": r["genre"], "title": title or r["title"], "url": r["url"],
                "date_inventory": r["published_date"], "date_meta": date_meta, "date_meta_key": date_key,
                "include_v1": r["include_v1"], "word_count": len(text.split()),
                "text": text, "engine": used,
                "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            jl.write(json.dumps(rec, ensure_ascii=False) + "\n"); jl.flush()
            log.writerow([did, r["url"], "ok", len(text), date_meta, date_key, used, rec["scraped_at"]])
            print(f"[{i}/{len(rows)}] ok  {len(text):>6} chars  {r['company']:9} {r['title'][:60]}")
        except Exception as e:  # keep going; failures are logged for a retry pass
            log.writerow([did, r["url"], f"error: {e}"[:200], 0, "", "", engine,
                          datetime.now(timezone.utc).isoformat(timespec="seconds")])
            print(f"[{i}/{len(rows)}] ERR {r['url']} -> {e}", file=sys.stderr)
        time.sleep(a.sleep)


if __name__ == "__main__":
    main()
