#!/usr/bin/env python3
"""News + government-policy layers for the frontier AI discourse map (Sep 2025 -> Sep 2026).

Reuses discovery/fetch/extract helpers from collect_year.py. Writes under layers/<layer>/ in the
same shape as year1/ so clean.py and the phase-4 coding can run unchanged.

Usage (from the project folder, in Terminal):
    python3 -m pip install --user requests trafilatura python-dotenv
    cp layers.env.example layers.env          # then paste your two keys into layers.env
    python3 scripts/collect_layers.py --smoke                  # ~2 docs per source, quick check
    python3 scripts/collect_layers.py                          # full run (resumable)
    python3 scripts/collect_layers.py --layer policy           # one layer only
    python3 scripts/collect_layers.py --stage discover         # only build candidate lists

Stages: discover (sitemaps / feeds / APIs -> candidates.csv) -> sample (cap per source x month,
oversampled for failures) -> fetch -> extract + AI filter -> keep cap -> corpus_raw.jsonl.
Pages plain fetch cannot read go to firecrawl_todo.csv (run scrape.py on it, as for year1).

Caps: news 8 per outlet x month, policy 10 per jurisdiction x month.
Keys (free): GUARDIAN_API_KEY (open-platform.theguardian.com), DATA_GOV_API_KEY (api.data.gov,
used for GovInfo: congressional bills and hearings). Federal Register needs no key.
"""
import argparse, csv, gzip, json, os, random, re, sys, time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect_year as cy  # noqa: E402

for f in (ROOT / "layers.env", ROOT / ".env"):
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# URL-slug pre-filter: keeps fetch volume sane on big news sitemaps
SLUG_AI = re.compile(r"(?:^|[/\-_])(ai|a-i|agi|openai|anthropic|claude|chatgpt|gpt-?\d*[a-z]?|gemini|deepmind|copilot|"
                     r"llama|meta-ai|nvidia|llms?|chatbots?|artificial-intelligence|machine-learning|agents?|agentic|"
                     r"altman|amodei|hassabis|nadella|zuckerberg|jassy|pichai|sora|grok|xai|superintelligence|"
                     r"data-?centers?|frontier-models?|ai-act|gpai|ai-office|algorithm\w*|deepfakes?|automation)(?:$|[/\-_])", re.I)
AI_TERMS = cy.AI_TERMS
TITLE_AI = re.compile(r"\b(AI|A\.I\.|AGI|OpenAI|Anthropic|Claude|ChatGPT|GPT-?\d|Gemini|DeepMind|Copilot|Llama|Nvidia|LLMs?|chatbots?|"
                      r"artificial intelligence|machine learning|agents?|agentic|Altman|Amodei|Hassabis|Nadella|Zuckerberg|Sora|Grok|xAI|"
                      r"superintelligence|data cent(er|re)s?|frontier models?|AI Act|deepfakes?|algorithms?)\b", re.I)
COMPANY = re.compile(r"\b(OpenAI|Anthropic|Google|DeepMind|Gemini|Meta|Microsoft|Amazon|AWS|ChatGPT|Claude|Copilot|Llama)\b")

# ------------------------------------------------------------------ sources
# kind: sitemap (host sitemaps + optional feeds, include regex, slug filter) | guardian | fedreg | govinfo
SRC = [
    # ---- news: tech press + open access
    dict(layer="news", source="The Verge", cell="The Verge", kind="sitemap", hosts=["www.theverge.com"],
         feeds=["https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"],
         include=r"^https://www\.theverge\.com/(?:[a-z0-9\-]+/)*\d{5,}/[a-z0-9\-]+/?$", exclude=r"/(authors?|tags?)/", slug=True),
    dict(layer="news", source="TechCrunch", cell="TechCrunch", kind="sitemap", hosts=["techcrunch.com"], sitemap=False, paged=True,
         feeds=["https://techcrunch.com/category/artificial-intelligence/feed/"],
         include=r"^https://techcrunch\.com/20\d\d/\d\d/\d\d/[a-z0-9\-]+/?$", exclude=None, slug=True),
    dict(layer="news", source="Wired", cell="Wired", kind="sitemap", hosts=["www.wired.com"],
         feeds=["https://www.wired.com/feed/tag/ai/latest/rss"],
         include=r"^https://www\.wired\.com/story/[a-z0-9\-]+/?$", exclude=None, slug=True),
    dict(layer="news", source="Ars Technica", cell="Ars Technica", kind="sitemap", hosts=["arstechnica.com"], sitemap=False, paged=True,
         feeds=["https://arstechnica.com/ai/feed/"],
         include=r"^https://arstechnica\.com/[a-z\-]+/20\d\d/\d\d/[a-z0-9\-]+/?$", exclude=None, slug=True),
    dict(layer="news", source="VentureBeat", cell="VentureBeat", kind="sitemap", hosts=["venturebeat.com"], sitemap=False, paged=True,
         feeds=["https://venturebeat.com/category/ai/feed/"],
         include=r"^https://venturebeat\.com/[a-z\-]+/[a-z0-9\-]+/?$", exclude=r"/(author|tag|category|page)/", slug=True),
    dict(layer="news", source="MIT Technology Review", cell="MIT Technology Review", kind="sitemap",
         hosts=["www.technologyreview.com"], feeds=["https://www.technologyreview.com/topic/artificial-intelligence/feed"],
         include=r"^https://www\.technologyreview\.com/20\d\d/\d\d/\d\d/\d+/[a-z0-9\-]+/?$", exclude=None, slug=True),
    dict(layer="news", source="BBC", cell="BBC", kind="sitemap", hosts=["www.bbc.com"],
         feeds=["https://feeds.bbci.co.uk/news/technology/rss.xml"],
         include=r"^https://www\.bbc\.com/news/articles/[a-z0-9]+/?$", exclude=None, slug=False, oversample=6),
    dict(layer="news", source="The Guardian", cell="The Guardian", kind="guardian"),
    # ---- policy: US federal
    dict(layer="policy", source="Federal Register", cell="US federal", kind="fedreg", jurisdiction="US federal"),
    dict(layer="policy", source="White House", cell="US federal", kind="sitemap", jurisdiction="US federal",
         hosts=["www.whitehouse.gov"], feeds=[],
         include=r"^https://www\.whitehouse\.gov/(presidential-actions|briefings-statements|articles|fact-sheets|remarks|releases)/20\d\d/\d\d/[a-z0-9\-]+/?$",
         exclude=None, slug=True),
    dict(layer="policy", source="NIST", cell="US federal", kind="sitemap", jurisdiction="US federal",
         hosts=["www.nist.gov"], feeds=["https://www.nist.gov/news-events/news/rss.xml"],
         include=r"^https://www\.nist\.gov/(news-events/news/20\d\d/\d\d/[a-z0-9\-]+|caisi/[a-z0-9\-/]+|artificial-intelligence/[a-z0-9\-/]+)/?$",
         exclude=None, slug=True),
    dict(layer="policy", source="FTC", cell="US federal", kind="sitemap", jurisdiction="US federal",
         hosts=["www.ftc.gov"], feeds=["https://www.ftc.gov/feeds/press-release.xml"],
         include=r"^https://www\.ftc\.gov/news-events/news/press-releases/20\d\d/\d\d/[a-z0-9\-]+/?$", exclude=None, slug=True),
    # ---- policy: US Congress + California
    dict(layer="policy", source="Congress (GovInfo)", cell="US Congress", kind="govinfo", jurisdiction="US Congress"),
    dict(layer="policy", source="California Governor", cell="US California", kind="sitemap", jurisdiction="US California",
         hosts=["www.gov.ca.gov"], feeds=["https://www.gov.ca.gov/feed/"],
         include=r"^https://www\.gov\.ca\.gov/20\d\d/\d\d/\d\d/[a-z0-9\-]+/?$", exclude=None, slug=True),
    # ---- policy: EU
    dict(layer="policy", source="European Commission (digital strategy)", cell="EU", kind="sitemap", jurisdiction="EU",
         hosts=["digital-strategy.ec.europa.eu"], feeds=["https://digital-strategy.ec.europa.eu/en/rss.xml"],
         include=r"^https://digital-strategy\.ec\.europa\.eu/en/(news|library|events|faqs|policies)/[a-z0-9\-]+/?$", exclude=None, slug=True),
    dict(layer="policy", source="European Parliament", cell="EU", kind="sitemap", jurisdiction="EU", sitemap=False,
         hosts=["www.europarl.europa.eu"], feeds=["https://www.europarl.europa.eu/rss/doc/press-releases/en.xml"],
         include=r"^https://www\.europarl\.europa\.eu/news/en/(press-room|headlines)/[^/]+/[a-z0-9\-]+/?$", exclude=None, slug=True),
]
SRC.insert(next(i for i, x in enumerate(SRC) if x["source"] == "European Parliament") + 1,
    dict(layer="policy", source="Council of the EU", cell="EU", kind="sitemap", jurisdiction="EU",
         hosts=["www.consilium.europa.eu"], feeds=["https://www.consilium.europa.eu/en/rss/pressreleases.ashx"],
         include=r"^https://www\.consilium\.europa\.eu/en/press/press-releases/20\d\d/\d\d/\d\d/[a-z0-9\-]+/?$", exclude=None, slug=True))
CAP = {"news": 8, "policy": 10}
OVERSAMPLE = 2.5


def months(start, end):
    d = date(start.year, start.month, 1)
    while d <= end:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        yield d, nxt - timedelta(days=1)
        d = nxt


# ------------------------------------------------------------------ discovery
SKIP_CHILD = re.compile(r"(?:^|[/_\-])(pages?|tags?|category|categories|authors?|users?|videos?|images?|attachments?|products?|topics?|"
                        r"galler(?:y|ies)|podcasts?|events?|jobs?|newsletters?|taxonomy|people|series)(?:[/_\-.]|sitemap|$)", re.I)
OLD_YEAR = re.compile(r"(?<!\d)(19\d\d|20[01]\d|202[0-4])(?!\d)")


QY = re.compile(r"[?&]yyyy=(\d{4})(?:&mm=(\d{1,2}))?")


def before_window(loc, start):
    m = QY.search(loc)
    if not m:
        return False
    y, mo = int(m.group(1)), int(m.group(2) or 12)
    return (y, mo) < (start.year, start.month)


def walk(url, start, seen, out, depth=0, log=print):
    if url in seen or depth > 4:
        return
    seen.add(url)
    for attempt in range(3):
        try:
            r = cy.get(url)
            if r.status_code in (403, 429, 503):
                time.sleep(15 * (attempt + 1)); continue
            if r.status_code != 200:
                log(f"    sitemap {r.status_code}: {url}"); return
            root = cy.xml_root(r.content, url); break
        except Exception as e:
            # malformed XML: fall back to regex over <loc>/<lastmod>
            try:
                raw = r.content
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                txt = raw.decode("utf-8", "replace")
                is_index = "<sitemapindex" in txt
                blocks = re.findall(r"<(?:sitemap|url)>(.*?)</(?:sitemap|url)>", txt, re.S)
                log(f"    sitemap malformed, regex fallback ({len(blocks)} entries): {url}")
                for b in blocks:
                    m = re.search(r"<loc>\s*(.*?)\s*</loc>", b, re.S)
                    if not m:
                        continue
                    loc = m.group(1).replace("&amp;", "&"); lm = cy.iso((re.search(r"<lastmod>(.*?)</lastmod>", b) or [None, ""])[1])
                    if is_index:
                        path = urlparse(loc).path
                        if (lm and lm < start.isoformat()) or SKIP_CHILD.search(path) or OLD_YEAR.search(loc) or before_window(loc, start):
                            continue
                        walk(loc, start, seen, out, depth + 1, log)
                    else:
                        out.append((loc, lm))
            except Exception as e2:
                log(f"    sitemap error {url}: {e} / {e2}")
            return
    else:
        log(f"    sitemap gave up (blocked): {url}"); return
    time.sleep(0.4)
    kind = cy.strip_ns(root.tag)
    for node in root:
        loc = lastmod = None
        for c in node:
            t = cy.strip_ns(c.tag)
            if t == "loc": loc = (c.text or "").strip()
            elif t == "lastmod": lastmod = cy.iso(c.text)
        if not loc:
            continue
        if kind == "sitemapindex":
            path = urlparse(loc).path
            if (lastmod and lastmod < start.isoformat()) or SKIP_CHILD.search(path) or OLD_YEAR.search(loc) or before_window(loc, start) \
                    or cy.NON_EN_SITEMAP.search(path):
                continue
            walk(loc, start, seen, out, depth + 1, log)
        else:
            out.append((loc, lastmod))


def read_paged(feed, start, log=print):
    items, page = [], 1
    while page <= 400:
        u = feed if page == 1 else f"{feed}{'&' if '?' in feed else '?'}paged={page}"
        new = []
        for attempt in range(3):
            got = cy.read_feed(u, log=lambda m: None)
            new = [it for it in got if cy.key(it["url"]) not in {cy.key(x["url"]) for x in items}]
            if new:
                break
            time.sleep(20 * (attempt + 1))
        if not new:
            log(f"    paged feed stopped at page {page} (empty or blocked)")
            break
        items += new
        ds = [it["date"] for it in new if it["date"]]
        if ds and min(ds) < start.isoformat():
            break
        page += 1; time.sleep(0.6)
    log(f"    paged feed {feed}: {len(items)} items, {page} pages")
    return items


def discover_sitemap(s, start, end, cache):
    rows, seen_u = [], set()
    inc = re.compile(s["include"]); exc = re.compile(s["exclude"]) if s.get("exclude") else None
    feed = {}
    for fu in s.get("feeds", []):
        for it in (read_paged(fu, start) if s.get("paged") else cy.read_feed(fu)):
            feed[cy.key(it["url"])] = it
    fdates = sorted(it["date"] for it in feed.values() if it.get("date"))
    # paged section feeds often stop after a few pages: fall back to the sitemap for the rest of the year
    use_sitemap = s.get("sitemap", True) or not fdates or fdates[0] > (start + timedelta(days=21)).isoformat()
    if s.get("paged") and use_sitemap and not s.get("sitemap", True):
        print(f"    feed reaches back only to {fdates[0] if fdates else 'n/a'}: also walking the sitemap")
    pool = []
    for h in (s["hosts"] if use_sitemap else []):
        f = cache / f"{h}.csv"
        if f.exists():
            pool += [tuple(r) for r in csv.reader(open(f, encoding="utf-8"))]
        else:
            out, seen = [], set()
            for sm in cy.sitemaps_for(h):
                walk(sm, start, seen, out, log=lambda m: print(f"  [{h}] {m.strip()}"))
            with open(f, "w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerows(out)
            pool += out
    pool += [(it["url"], None) for it in feed.values()]
    for u, lm in pool:
        u = cy.norm(u)
        if cy.key(u) in seen_u or not inc.search(u) or (exc and exc.search(u)):
            continue
        fm = feed.get(cy.key(u), {})
        from_ai_feed = bool(fm) and s.get("paged")
        if s.get("slug") and not from_ai_feed and not (SLUG_AI.search(urlparse(u).path) or TITLE_AI.search(fm.get("title") or "")):
            continue
        ud, fd = cy.url_date(u), fm.get("date")
        if ud and not (start.isoformat()[:7] <= ud[:7] <= end.isoformat()[:7]):
            continue
        if fd and not (start.isoformat() <= fd <= end.isoformat()):
            continue
        if not ud and not fd and lm and lm < start.isoformat():
            continue
        seen_u.add(cy.key(u))
        guess = fd or (ud if ud and not ud.endswith("-01") else "") or (lm or "")
        rows.append(dict(url=u, title=fm.get("title") or "", date_guess=guess, text="", doc_type=""))
    return rows


def discover_guardian(s, start, end):
    key = os.environ.get("GUARDIAN_API_KEY")
    if not key:
        print("  ! GUARDIAN_API_KEY missing in layers.env: skipping The Guardian"); return []
    q = ('"artificial intelligence" OR OpenAI OR Anthropic OR ChatGPT OR DeepMind OR Gemini OR Copilot '
         'OR "Meta AI" OR "large language model" OR chatbot')
    rows = []
    for a, b in months(start, end):
        for page in (1, 2):
            r = requests.get("https://content.guardianapis.com/search", timeout=45, params=dict(
                q=q, **{"from-date": max(a, start).isoformat(), "to-date": min(b, end).isoformat()},
                **{"page-size": 50, "page": page, "show-fields": "headline,bodyText,byline", "order-by": "relevance",
                   "api-key": key}))
            if r.status_code != 200:
                print(f"  guardian {a:%Y-%m} p{page}: HTTP {r.status_code}"); break
            res = r.json().get("response", {}).get("results", [])
            for it in res:
                fl = it.get("fields", {})
                rows.append(dict(url=it["webUrl"], title=fl.get("headline") or it.get("webTitle", ""),
                                 date_guess=it.get("webPublicationDate", "")[:10], text=fl.get("bodyText", ""),
                                 doc_type=it.get("sectionName", "")))
            time.sleep(1.1)
            if len(res) < 50:
                break
    return rows


def discover_fedreg(s, start, end):
    rows = []
    for a, b in months(start, end):
        r = requests.get("https://www.federalregister.gov/api/v1/documents.json", timeout=60, params=[
            ("conditions[term]", '"artificial intelligence"'),
            ("conditions[publication_date][gte]", max(a, start).isoformat()),
            ("conditions[publication_date][lte]", min(b, end).isoformat()),
            ("per_page", "100"), ("order", "relevance"),
            *[("fields[]", f) for f in ("title", "type", "agencies", "publication_date", "html_url", "raw_text_url", "abstract")]])
        if r.status_code != 200:
            print(f"  fedreg {a:%Y-%m}: HTTP {r.status_code}"); continue
        for it in r.json().get("results", []):
            ag = ", ".join(x.get("name", "") for x in (it.get("agencies") or []) if x)
            rows.append(dict(url=it["html_url"], title=it["title"], date_guess=it["publication_date"],
                             text="", doc_type=f"{it.get('type','')} · {ag}", raw_text_url=it.get("raw_text_url", ""),
                             abstract=it.get("abstract") or ""))
        time.sleep(0.6)
    return rows


def discover_govinfo(s, start, end):
    key = os.environ.get("DATA_GOV_API_KEY")
    if not key:
        print("  ! DATA_GOV_API_KEY missing in layers.env: skipping Congress"); return []
    rows = []
    for a, b in months(start, end):
        body = {"query": f'"artificial intelligence" collection:(BILLS OR CHRG) '
                         f'publishdate:range({max(a, start).isoformat()},{min(b, end).isoformat()})',
                "pageSize": 100, "offsetMark": "*", "sorts": [{"field": "score", "sortOrder": "DESC"}]}
        r = requests.post(f"https://api.govinfo.gov/search?api_key={key}", json=body, timeout=60)
        if r.status_code != 200:
            print(f"  govinfo {a:%Y-%m}: HTTP {r.status_code} {r.text[:120]}"); continue
        for it in r.json().get("results", []):
            pid = it.get("packageId", "")
            gid = it.get("granuleId") or pid
            coll = it.get("collectionCode", "")
            rows.append(dict(url=f"https://www.govinfo.gov/app/details/{pid}" + (f"/{gid}" if gid != pid else ""),
                             title=it.get("title", ""), date_guess=(it.get("dateIssued") or "")[:10], text="",
                             doc_type="bill" if coll == "BILLS" else "hearing" if coll == "CHRG" else coll,
                             raw_text_url=f"https://www.govinfo.gov/content/pkg/{pid}/html/{gid}.htm"))
        time.sleep(1.0)
    return rows


def discover(layers, start, end, P):
    cache = ROOT / "layers" / "_sitemaps"; cache.mkdir(parents=True, exist_ok=True)
    out = []
    for s in SRC:
        if s["layer"] not in layers:
            continue
        print(f"[discover] {s['layer']:6} {s['source']}")
        try:
            fn = {"sitemap": lambda: discover_sitemap(s, start, end, cache), "guardian": lambda: discover_guardian(s, start, end),
                  "fedreg": lambda: discover_fedreg(s, start, end), "govinfo": lambda: discover_govinfo(s, start, end)}[s["kind"]]
            rows = fn()
        except Exception as e:
            print(f"  ! {s['source']} failed: {type(e).__name__}: {e}"); rows = []
        for r in rows:
            r.update(layer=s["layer"], source=s["source"], cell=s["cell"], jurisdiction=s.get("jurisdiction", ""),
                     kind=s["kind"], doc_id=cy.did(r["url"]))
        print(f"    {len(rows)} candidates")
        out += rows
    cols = ["layer", "source", "cell", "jurisdiction", "kind", "doc_id", "url", "title", "date_guess", "doc_type",
            "raw_text_url", "abstract", "text"]
    with open(P["cand"], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in out:
            w.writerow({c: r.get(c, "") for c in cols})
    return out


# ------------------------------------------------------------------ sample, fetch, extract
def sample(rows, smoke):
    rng = random.Random(20261007)
    by = defaultdict(list)
    for r in rows:
        by[(r["layer"], r["cell"], r["source"], (r["date_guess"] or "")[:7])].append(r)
    keep = []
    for (layer, cell, src, mo), rs in sorted(by.items()):
        n_src = len({k[2] for k in by if k[0] == layer and k[1] == cell}) or 1
        s = next(x for x in SRC if x["source"] == src)
        k = 2 if smoke else int(round(CAP[layer] * s.get("oversample", OVERSAMPLE) / n_src + 0.49))
        rng.shuffle(rs)
        keep += rs[:k]
    if smoke:  # 2 per source overall
        c, out = Counter(), []
        for r in keep:
            if c[r["source"]] < 2:
                out.append(r); c[r["source"]] += 1
        keep = out
    return keep


def fetch_text_urls(rows, P):
    """Federal Register raw text and GovInfo HTML: one polite sequential pass."""
    s = requests.Session(); s.headers.update(cy.HDR)
    key = os.environ.get("DATA_GOV_API_KEY", "")
    for r in rows:
        f = P["html"] / f"{r['doc_id']}.html.gz"
        if f.exists() or not r.get("raw_text_url"):
            continue
        try:
            resp = s.get(r["raw_text_url"], timeout=60)
            if resp.status_code == 200:
                f.write_bytes(gzip.compress(resp.content))
            else:
                (P["html"] / f"{r['doc_id']}.fail").write_text(str(resp.status_code))
        except Exception as e:
            (P["html"] / f"{r['doc_id']}.fail").write_text(type(e).__name__)
        time.sleep(0.8)


def plain_text(raw):
    t = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", raw)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", t)).strip()


def extract_all(rows, start, end, P):
    keep_ids = {json.loads(l)["doc_id"] for l in open(P["jsonl"], encoding="utf-8")} if P["jsonl"].exists() else set()
    jl = open(P["jsonl"], "a", encoding="utf-8")
    inv, fc, st = [], [], Counter()
    for r in rows:
        text, title, d, src_d = r.get("text") or "", r.get("title") or "", None, ""
        if r["kind"] == "guardian":
            d, src_d = r["date_guess"], "api"
        else:
            hf = P["html"] / f"{r['doc_id']}.html.gz"
            if not hf.exists():
                fc.append(r); st["firecrawl-needed"] += 1; continue
            html = gzip.decompress(hf.read_bytes()).decode("utf-8", "replace")
            if r["kind"] in ("fedreg", "govinfo"):
                text = plain_text(html); d, src_d = r["date_guess"], "api"
                if r["kind"] == "fedreg" and r.get("abstract"):
                    text = r["abstract"] + "\n\n" + text
            else:
                text, t2, lang = cy.extract(html)
                title = t2 or title
                md, _ = cy.meta_date(html)
                d = md or (cy.url_date(r["url"]) if cy.url_date(r["url"]) and not cy.url_date(r["url"]).endswith("-01") else None) \
                    or cy.visible_date(text) or (r["date_guess"] or None)
                src_d = "meta" if md else "other"
                if lang and not lang.startswith("en"):
                    st["non-English"] += 1; continue
        words = len(text.split())
        if not d or not (start.isoformat() <= d[:10] <= end.isoformat()):
            st["out of window / no date"] += 1; continue
        if words < 120:
            if r["kind"] == "sitemap":
                fc.append(r); st["firecrawl-needed (thin)"] += 1
            else:
                st["too short"] += 1
            continue
        hits = len(AI_TERMS.findall(text)); dens = 1000 * hits / max(words, 1)
        if hits < 3 or (dens < 2 and not COMPANY.search(text[:3000])):
            st["not AI"] += 1; continue
        r.update(date=d[:10], date_source=src_d, title_final=title, words=words, ai_density=round(dens, 1))
        inv.append(r); st["ok"] += 1
        if r["doc_id"] not in keep_ids:
            (P["raw"] / f"{r['doc_id']}.md").write_text(text, encoding="utf-8")
            jl.write(json.dumps(dict(doc_id=r["doc_id"], layer=r["layer"], source=r["source"], cell=r["cell"],
                                     jurisdiction=r["jurisdiction"], channel=r["source"], company=r["source"],
                                     doc_type=r.get("doc_type", ""), title=title, url=r["url"], date=d[:10],
                                     date_source=src_d, word_count=words, ai_density=round(dens, 1), text=text,
                                     engine="api" if r["kind"] != "sitemap" else "trafilatura",
                                     scraped_at=datetime.now(timezone.utc).isoformat(timespec="seconds")),
                                ensure_ascii=False) + "\n")
    jl.close()
    with open(P["fc"], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["company", "channel", "title", "url", "published_date", "include_v1", "layer", "cell"])
        w.writeheader()
        for r in fc:
            w.writerow(dict(company=r["source"], channel=r["source"], title=r.get("title", ""), url=r["url"],
                            published_date=r.get("date_guess", ""), include_v1="review", layer=r["layer"], cell=r["cell"]))
    print(f"[extract] {dict(st)}")
    return inv, fc


def final_cap(P, start, end):
    """Keep at most CAP docs per layer x cell x month (random, seeded) -> corpus_capped.jsonl."""
    if not P["jsonl"].exists():
        return
    rng = random.Random(7)
    by = defaultdict(list)
    for l in open(P["jsonl"], encoding="utf-8"):
        d = json.loads(l); by[(d["layer"], d["cell"], d["date"][:7])].append(d)
    out = []
    for (layer, cell, mo), ds in sorted(by.items()):
        rng.shuffle(ds)
        for d in ds[:CAP[layer]]:
            d["weight"] = round(len(ds) / min(len(ds), CAP[layer]), 3); out.append(d)
    with open(P["capped"], "w", encoding="utf-8") as f:
        for d in out:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    c = Counter((d["layer"], d["cell"]) for d in out)
    print("[cap] kept per layer x cell:")
    for k, v in sorted(c.items()):
        print(f"    {k[0]:6} {k[1]:24} {v}")
    print(f"[cap] {len(out)} docs -> {P['capped'].relative_to(ROOT)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", choices=["news", "policy", "both"], default="both")
    ap.add_argument("--start", default="2025-09-25"); ap.add_argument("--end", default="2026-09-25")
    ap.add_argument("--stage", choices=["all", "discover", "fetch", "extract", "cap"], default="all")
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    layers = ["news", "policy"] if a.layer == "both" else [a.layer]
    base = ROOT / ("layers_smoke" if a.smoke else "layers")
    P = dict(cand=base / "phase1" / "candidates.csv", samp=base / "phase1" / "sampled.csv", html=base / "html",
             raw=base / "phase2" / "raw", jsonl=base / "phase2" / "corpus_raw.jsonl",
             capped=base / "phase2" / "corpus_capped.jsonl", fc=base / "phase1" / "firecrawl_todo.csv")
    for d in (base / "phase1", P["html"], P["raw"]):
        d.mkdir(parents=True, exist_ok=True)
    print(f"window {start} .. {end}  layers {layers}  ->  {base.relative_to(ROOT)}")
    if a.stage in ("all", "discover") or not P["cand"].exists():
        rows = discover(layers, start, end, P)
    else:
        rows = [r for r in csv.DictReader(open(P["cand"], encoding="utf-8")) if r["layer"] in layers]
    if a.stage == "discover":
        return
    if a.stage == "cap":
        final_cap(P, start, end); return
    samp = sample(rows, a.smoke)
    print(f"[sample] {len(samp)} to fetch/read  " + str(dict(Counter((r['layer'], r['cell']) for r in samp))))
    if a.stage in ("all", "fetch"):
        cy.fetch_all([r for r in samp if r["kind"] == "sitemap"], dict(html=P["html"]), a.delay)
        fetch_text_urls([r for r in samp if r["kind"] in ("fedreg", "govinfo")], P)
    if a.stage in ("all", "fetch", "extract"):
        inv, fc = extract_all(samp, start, end, P)
        final_cap(P, start, end)
        if fc:
            print(f"\nNext: {len(fc)} pages need Firecrawl:\n  python3 scripts/scrape.py --inv {P['fc'].relative_to(ROOT)} "
                  f"--out {(base / 'phase2_firecrawl').relative_to(ROOT)} --engine firecrawl")


if __name__ == "__main__":
    main()
