#!/usr/bin/env python3
"""Year-scale collection: discover -> fetch -> date -> extract, for the 6 FMF companies.

Replaces the manual phase-1 inventory for long windows. Same channels and exclusion
rules as the pilot (see PROCESS_LOG.md). Pilot files are never touched; everything
goes under --root (default: year1/).

Usage (from the project folder, in Terminal):
    python3 scripts/collect_year.py --smoke            # 3 URLs per channel, quick check
    python3 scripts/collect_year.py                    # full run (resumable; re-run to continue)
    python3 scripts/collect_year.py --stage discover   # only build the candidate list

Stages
  1. discover : sitemaps (via robots.txt) + RSS/Atom feeds -> year1/phase1/candidates.csv
               (pre-filter: URL pattern per channel; URL date or sitemap lastmod >= start)
  2. fetch    : plain HTTP fetch, one thread per host, polite delay; gz cache in year1/html/
  3. date+extract : published date (meta/JSON-LD > URL > visible byline > feed > lastmod),
               trafilatura main text, language, AI-keyword density, exclusion rules
               -> year1/phase1/url_inventory.csv  (same columns as the pilot inventory)
               -> year1/phase2/raw/<doc_id>.md + corpus_raw.jsonl (same format as scrape.py)
               -> year1/phase1/firecrawl_todo.csv (pages plain fetch could not read)
Then run scrape.py on firecrawl_todo.csv (see printed instructions at the end).
"""
import argparse, csv, gzip, hashlib, io, json, re, sys, threading, time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parent.parent
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36 (research crawler; MIT Media Lab Viral Communications)")
HDR = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}

# ---------------------------------------------------------------- channels
# include: regex the URL must match; exclude: regex that drops it; ai_filter: channel is
# "AI yes only" in the pilot -> non-AI posts are dropped by keyword density.
CH = [
    dict(company="OpenAI", channel="newsroom", hosts=["openai.com"],
         feeds=["https://openai.com/news/rss.xml"],
         include=r"^https://openai\.com/index/[a-z0-9\-]+/?$", exclude=None, ai_filter=False),
    dict(company="OpenAI", channel="ceo-blog", hosts=["blog.samaltman.com"],
         feeds=["https://blog.samaltman.com/posts.atom"],
         include=r"^https?://blog\.samaltman\.com/[a-z0-9\-]+/?$", exclude=r"/(tagged|page|archive)", ai_filter=False),
    dict(company="Anthropic", channel="newsroom", hosts=["www.anthropic.com"], feeds=[],
         include=r"^https://www\.anthropic\.com/(news/[a-z0-9\-]+|features/[a-z0-9\-]+|claude-[a-z0-9\-]+|[a-z0-9\-]*report[a-z0-9\-]*)/?$",
         exclude=r"/(news|features)/?$", ai_filter=False),
    dict(company="Anthropic", channel="research", hosts=["www.anthropic.com"], feeds=[],
         include=r"^https://www\.anthropic\.com/research/[a-z0-9\-]+/?$", exclude=r"/research/team", ai_filter=False),
    dict(company="Anthropic", channel="ceo-essays", hosts=["darioamodei.com"], feeds=[],
         include=r"^https://(www\.)?darioamodei\.com/(post|essay)/[a-z0-9\-]+/?$", exclude=None, ai_filter=False),
    dict(company="Google", channel="blog.google", hosts=["blog.google"], feeds=["https://blog.google/rss/"],
         include=r"^https://blog\.google/(?!intl/)[a-z0-9\-]+/.+/[a-z0-9\-]+/$",
         exclude=r"(developers-tools|/authors/|/topics/|/rss/|/intl/)", ai_filter=True),
    dict(company="Google", channel="deepmind.google", hosts=["deepmind.google"],
         feeds=["https://deepmind.google/blog/rss.xml"],
         include=r"^https://deepmind\.google/(discover/)?blog/[a-z0-9\-]+/?$", exclude=None, ai_filter=False),
    dict(company="Google", channel="research.google", hosts=["research.google"],
         feeds=["https://research.google/blog/rss/"],
         include=r"^https://research\.google/blog/[a-z0-9\-]+/?$", exclude=r"/blog/(label|page)/", ai_filter=False),
    dict(company="Meta", channel="newsroom", hosts=["about.fb.com"], feeds=["https://about.fb.com/feed/"],
         include=r"^https://about\.fb\.com/news/20\d\d/\d\d/[a-z0-9\-]+/?$", exclude=None, ai_filter=True),
    dict(company="Meta", channel="meta-ai-blog", hosts=["ai.meta.com"], feeds=[],
         include=r"^https://ai\.meta\.com/blog/[a-z0-9\-]+/?$", exclude=None, ai_filter=False),
    dict(company="Microsoft", channel="official-blog", hosts=["blogs.microsoft.com"],
         feeds=["https://blogs.microsoft.com/feed/"],
         include=r"^https://blogs\.microsoft\.com/blog/20\d\d/\d\d/\d\d/[a-z0-9\-]+/?$", exclude=None, ai_filter=False),
    dict(company="Microsoft", channel="on-the-issues", hosts=["blogs.microsoft.com"],
         feeds=["https://blogs.microsoft.com/on-the-issues/feed/"],
         include=r"^https://blogs\.microsoft\.com/on-the-issues/20\d\d/\d\d/\d\d/[a-z0-9\-]+/?$", exclude=None, ai_filter=True),
    dict(company="Microsoft", channel="source", hosts=["news.microsoft.com"], feeds=["https://news.microsoft.com/source/feed/"],
         include=r"^https://news\.microsoft\.com/source/([a-z]+/)?(features/([a-z0-9\-]+/)?[a-z0-9\-]+|20\d\d/\d\d(/\d\d)?/[a-z0-9\-]+)/?$",
         exclude=None, ai_filter=True),
    dict(company="Microsoft", channel="research-blog", hosts=["www.microsoft.com"],
         feeds=["https://www.microsoft.com/en-us/research/feed/"],
         include=r"^https://www\.microsoft\.com/en-us/research/blog/[a-z0-9\-]+/?$", exclude=None, ai_filter=False),
    dict(company="Microsoft", channel="microsoft-ai", hosts=["microsoft.ai"], feeds=[],
         include=r"^https://microsoft\.ai/news/[a-z0-9\-]+/?$", exclude=None, ai_filter=False),
    dict(company="Amazon", channel="about-amazon", hosts=["www.aboutamazon.com"],
         feeds=["https://www.aboutamazon.com/news/rss"],
         include=r"^https://www\.aboutamazon\.com/news/[a-z0-9\-]+/[a-z0-9\-]+/?$", exclude=None, ai_filter=True),
    dict(company="Amazon", channel="amazon-science", hosts=["www.amazon.science"],
         feeds=["https://www.amazon.science/index.rss"],
         include=r"^https://www\.amazon\.science/(blog|news)/[a-z0-9\-]+/?$", exclude=None, ai_filter=False),
    dict(company="Amazon", channel="aws-news-blog", hosts=["aws.amazon.com"],
         feeds=["https://aws.amazon.com/blogs/aws/feed/"],
         include=r"^https://aws\.amazon\.com/blogs/aws/[a-z0-9\-]+/?$",
         exclude=r"/blogs/aws/(category|tag|author|page)/", ai_filter=True),
    # --- NVIDIA (added 2026-09-30; not an FMF member, scope extension) ---
    dict(company="NVIDIA", channel="newsroom", hosts=["nvidianews.nvidia.com"],
         feeds=["https://nvidianews.nvidia.com/releases.xml", "https://nvidianews.nvidia.com/rss"],
         include=r"^https://nvidianews\.nvidia\.com/news/[a-z0-9\-]+/?$", exclude=None, ai_filter=True),
    dict(company="NVIDIA", channel="blog", hosts=["blogs.nvidia.com"], feeds=["https://blogs.nvidia.com/feed/"],
         include=r"^https://blogs\.nvidia\.com/blog/(20\d\d/\d\d/\d\d/)?[a-z0-9\-]+/?$",
         exclude=r"/blog/(category|tag|author|page)/|geforce-now-thursday", ai_filter=True),
]

# pilot exclusion rules, as patterns
ROUNDUP = re.compile(r"(weekly-roundup|weekly roundup|biggest news from|ai-updates-[a-z]+-20\d\d|"
                     r"\bupdates? (from|in) (january|february|march|april|may|june|july|august|"
                     r"september|october|november|december)\b|\brecap\b|\bround-?up\b)", re.I)
DEV = re.compile(r"(developers-tools|/developers?/|\bapi\b.*(now available|pricing)|\bsdk\b|codelab)", re.I)
CUSTOMER_HINT = re.compile(r'(/customer-stories/|/customers?/|digital-transformation/)', re.I)
AI_TERMS = re.compile(r"\b(AI|A\.I\.|artificial intelligence|machine learning|ML|model(s)?|LLMs?|"
                      r"generative|agentic|agents?|chatbots?|neural|deep learning|Gemini|Copilot|"
                      r"GPT[\w\-.]*|ChatGPT|Claude|Llama|Alexa\+?|Bedrock|Nova|DeepMind|Codex|inference|"
                      r"compute|GPUs?|TPUs?|training data|reinforcement learning|multimodal)\b")

DATE_KEYS = ["article:published_time", "og:published_time", "publishedTime", "datePublished",
             "parsely-pub-date", "sailthru.date", "dc.date", "pubdate", "publish-date", "date"]
MONTHS = "january february march april may june july august september october november december".split()
MON = {m[:3]: i for i, m in enumerate(MONTHS, 1)}
VIS_RE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),\s+(20\d\d)\b", re.I)
URL_DATE = re.compile(r"/(20\d\d)/(\d\d)(?:/(\d\d))?/")


def did(url):  # identical to scrape.py
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def norm(url):
    u = url.strip().split("#")[0].split("?")[0]
    return u


def key(url):
    return norm(url).rstrip("/").lower()


def iso(s):
    m = re.search(r"(20\d\d)-(\d\d)-(\d\d)", s or "")
    return m.group(0) if m else None


# ---------------------------------------------------------------- stage 1: discover
def get(url, timeout=45):
    return requests.get(url, headers=HDR, timeout=timeout)


def xml_root(content, url):
    if url.endswith(".gz") or content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return ET.fromstring(content)


def strip_ns(tag):
    return tag.split("}", 1)[-1]


def walk_sitemap(url, start, seen, out, depth=0, log=print):
    if url in seen or depth > 4:
        return
    seen.add(url)
    try:
        r = get(url)
        if r.status_code != 200:
            log(f"    sitemap {r.status_code}: {url}")
            return
        root = xml_root(r.content, url)
    except Exception as e:
        log(f"    sitemap error {url}: {e}")
        return
    kind = strip_ns(root.tag)
    for node in root:
        loc = lastmod = None
        for c in node:
            t = strip_ns(c.tag)
            if t == "loc":
                loc = (c.text or "").strip()
            elif t == "lastmod":
                lastmod = iso(c.text)
        if not loc:
            continue
        if kind == "sitemapindex":
            if lastmod and lastmod < start.isoformat():
                continue  # child sitemap untouched since before the window
            if NON_EN_SITEMAP.search(urlparse(loc).path):
                continue  # localized copies are excluded anyway
            walk_sitemap(loc, start, seen, out, depth + 1, log)
        else:
            out.append((loc, lastmod))


# hosts whose robots.txt lists the whole corporate site: go straight to the blog's own sitemap
SITEMAP_OVERRIDE = {
    "www.microsoft.com": ["https://www.microsoft.com/en-us/research/sitemap_index.xml"],
    "blogs.nvidia.com": ["https://blogs.nvidia.com/sitemap_index.xml"],   # robots.txt also lists 20 foreign blogs
}
# WordPress feeds can be paged (?paged=N) far back in time
PAGED_FEEDS = {"https://aws.amazon.com/blogs/aws/feed/", "https://www.microsoft.com/en-us/research/feed/",
               "https://blogs.microsoft.com/feed/", "https://blogs.microsoft.com/on-the-issues/feed/",
               "https://news.microsoft.com/source/feed/", "https://about.fb.com/feed/",
               "https://blogs.nvidia.com/feed/"}
NON_EN_SITEMAP = re.compile(r"[/_\-](?!en)(?:[a-z]{2}[-_][a-z]{2}|intl|ja|ko|zh|de|fr|es|pt|it)(?:[/_\-.])", re.I)


def sitemaps_for(host, log=print):
    if host in SITEMAP_OVERRIDE:
        return SITEMAP_OVERRIDE[host]
    sm = []
    try:
        r = get(f"https://{host}/robots.txt", timeout=20)
        if r.status_code == 200:
            sm = [l.split(":", 1)[1].strip() for l in r.text.splitlines() if l.lower().startswith("sitemap:")]
    except Exception as e:
        log(f"    robots.txt error {host}: {e}")
    if not sm:
        sm = [f"https://{host}/sitemap.xml", f"https://{host}/sitemap_index.xml"]
    return sm


def read_feed(url, log=print):
    items = []
    try:
        r = get(url)
        root = xml_root(r.content, url)
    except Exception as e:
        log(f"    feed error {url}: {e}")
        return items
    for it in root.iter():
        t = strip_ns(it.tag)
        if t not in ("item", "entry"):
            continue
        link = title = pub = None
        cats = []
        for c in it:
            ct = strip_ns(c.tag)
            if ct == "link":
                link = (c.text or "").strip() or c.attrib.get("href")
            elif ct == "title":
                title = (c.text or "").strip()
            elif ct in ("pubDate", "published", "date", "updated") and not pub:
                pub = c.text
            elif ct == "category":
                cats.append((c.text or c.attrib.get("term") or "").strip())
        if link:
            d = iso(pub)
            if not d and pub:
                try:
                    d = datetime.strptime(pub.strip()[:25].strip(), "%a, %d %b %Y %H:%M:%S").date().isoformat()
                except Exception:
                    d = None
            items.append(dict(url=link, title=title, date=d, cats="; ".join(cats)))
    return items


def u_path(url):
    return urlparse(url).path


def url_date(url):
    m = URL_DATE.search(url)
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2), m.group(3) or "01"
    return f"{y}-{mo}-{d}"


def discover(a, start, end, P):
    hosts = sorted({h for c in CH for h in c["hosts"]})
    cache = ROOT / a.root / "phase1" / "sitemaps"   # shared by smoke and full runs
    cache.mkdir(parents=True, exist_ok=True)
    urls_by_host = {}

    def one_host(h):
        f = cache / f"{h}.csv"
        if f.exists():
            return h, [tuple(r) for r in csv.reader(open(f, encoding="utf-8"))]
        out, seen = [], set()
        for sm in sitemaps_for(h):
            walk_sitemap(sm, start, seen, out, log=lambda m: print(f"  [{h}] {m.strip()}"))
        with open(f, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(out)
        return h, out

    print(f"[discover] sitemaps for {len(hosts)} hosts in parallel (cached in {cache.relative_to(ROOT)})")
    with ThreadPoolExecutor(max_workers=len(hosts)) as ex:
        for h, out in ex.map(one_host, hosts):
            urls_by_host[h] = out
            print(f"    {h:22} {len(out):>7} sitemap URLs")
    feed_meta = {}
    for c in CH:
        for f in c["feeds"]:
            items, page = read_feed(f), 1
            while f in PAGED_FEEDS and items and page < 150:
                dates = [it["date"] for it in items if it["date"]]
                if dates and min(dates) < start.isoformat():
                    break
                page += 1
                more = read_feed(f"{f}?paged={page}", log=lambda m: None)
                known = {key(it["url"]) for it in items}
                more = [it for it in more if key(it["url"]) not in known]
                if not more:
                    break
                items += more
                time.sleep(0.5)
            print(f"[feed] {f}: {len(items)} items" + (f" ({page} pages)" if page > 1 else ""))
            for it in items:
                feed_meta[key(it["url"])] = it
    rows, dup = [], set()
    for c in CH:
        inc = re.compile(c["include"]); exc = re.compile(c["exclude"]) if c["exclude"] else None
        pool = [(u, lm) for h in c["hosts"] for u, lm in urls_by_host.get(h, [])]
        pool += [(it["url"], None) for it in feed_meta.values() if urlparse(it["url"]).netloc in c["hosts"]]
        n = 0
        for u, lm in pool:
            u = norm(u)
            if not inc.search(u) or (exc and exc.search(u)) or key(u) in dup:
                continue
            ud = url_date(u)
            fm = feed_meta.get(key(u), {})
            fd = fm.get("date")
            # pre-filter: a known date (URL or feed) must be in window; lastmod is only an
            # upper bound on the publication date, so lastmod < start means out.
            if ud and not (start.isoformat()[:7] <= ud[:7] <= end.isoformat()[:7]):
                continue
            if fd and not (start.isoformat() <= fd <= end.isoformat()):
                continue
            if not ud and not fd and lm and lm < start.isoformat():
                continue
            dup.add(key(u))
            rows.append(dict(company=c["company"], channel=c["channel"], url=u, doc_id=did(u),
                             lastmod=lm or "", url_date=ud or "", feed_date=fd or "",
                             feed_title=fm.get("title") or "", feed_cats=fm.get("cats") or ""))
            n += 1
        print(f"  {c['company']:9} {c['channel']:16} {n} candidates")
    with open(P["cand"], "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["url"])
        w.writeheader(); w.writerows(rows)
    print(f"[discover] {len(rows)} candidates -> {P['cand']}")
    return rows


# ---------------------------------------------------------------- stage 2: fetch
def fetch_host(rows, P, delay, lock, stats):
    s = requests.Session(); s.headers.update(HDR)
    for r in rows:
        f = P["html"] / f"{r['doc_id']}.html.gz"
        if f.exists():
            continue
        status = "err"
        for attempt in range(3):
            try:
                resp = s.get(r["url"], timeout=45)
                status = str(resp.status_code)
                if resp.status_code == 200 and "html" in resp.headers.get("content-type", "html"):
                    f.write_bytes(gzip.compress(resp.content))
                    break
                if resp.status_code in (429, 500, 502, 503, 504):
                    time.sleep(int(resp.headers.get("Retry-After", 0) or 0) or 20 * (attempt + 1))
                    continue
                break
            except Exception as e:
                status = f"err:{type(e).__name__}"
                time.sleep(5)
        with lock:
            stats[status] += 1
            n = sum(stats.values())
            if n % 50 == 0:
                print(f"    fetched {n}  {dict(stats)}")
        if status != "200":
            (P["html"] / f"{r['doc_id']}.fail").write_text(status)
        time.sleep(delay)


def fetch_all(rows, P, delay):
    by_host = defaultdict(list)
    for r in rows:
        by_host[urlparse(r["url"]).netloc].append(r)
    todo = sum(1 for r in rows if not (P["html"] / f"{r['doc_id']}.html.gz").exists())
    print(f"[fetch] {todo} pages to fetch across {len(by_host)} hosts (delay {delay}s per host)")
    lock, stats = threading.Lock(), Counter()
    with ThreadPoolExecutor(max_workers=len(by_host)) as ex:
        for h, rs in by_host.items():
            ex.submit(fetch_host, rs, P, delay, lock, stats)
    print(f"[fetch] done {dict(stats)}")


# ---------------------------------------------------------------- stage 3: date + extract
def meta_date(html):
    for k in DATE_KEYS:
        m = re.search(rf'<meta[^>]+(?:property|name|itemprop)=["\']{re.escape(k)}["\'][^>]*content=["\']([^"\']+)', html, re.I) \
            or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name|itemprop)=["\']{re.escape(k)}["\']', html, re.I)
        if m and iso(m.group(1)):
            return iso(m.group(1)), k
    m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html) or re.search(r'"publishedAt"\s*:\s*"([^"]+)"', html) \
        or re.search(r'"publicationDate"\s*:\s*"([^"]+)"', html) or re.search(r'"publishDate"\s*:\s*"([^"]+)"', html)
    if m and iso(m.group(1)):
        return iso(m.group(1)), "json"
    m = re.search(r'<time[^>]+datetime=["\']([^"\']+)', html)
    if m and iso(m.group(1)):
        return iso(m.group(1)), "time-tag"
    return None, None


def visible_date(text):
    for m in VIS_RE.finditer(text[:3000]):
        pre = text[max(0, m.start() - 25):m.start()].lower()
        if "update" in pre:
            continue
        try:
            return date(int(m.group(3)), MON[m.group(1)[:3].lower()], int(m.group(2))).isoformat()
        except ValueError:
            continue
    return None


def extract(html):
    try:
        import trafilatura
    except ImportError:
        sys.exit("trafilatura missing: python3 -m pip install --user trafilatura")
    text = trafilatura.extract(html, output_format="markdown", include_links=False,
                               include_comments=False, favor_precision=True) or ""
    md = trafilatura.extract_metadata(html)
    title = (md.title if md else None) or ""
    lang = ""
    m = re.search(r'<html[^>]*\blang=["\']([A-Za-z\-]+)', html)
    if m:
        lang = m.group(1).lower()
    return text, title, lang


def classify(c, r, title, text, lang, html):
    """Return (include_v1, reason, genre_hint) using the pilot's rules."""
    u = r["url"]
    if lang and not lang.startswith("en"):
        return "N", "non-English", ""
    if ROUNDUP.search(u) or ROUNDUP.search(title):
        return "N", "digest/roundup (repeats other posts)", ""
    if DEV.search(u):
        return "N", "developer post (excluded)", ""
    if (r["company"] == "OpenAI" and "customer" in r.get("feed_cats", "").lower()) or CUSTOMER_HINT.search(u):
        return "N", "customer story (v1 excluded)", "customer-story"
    if c["ai_filter"] and text:
        words = max(len(text.split()), 1)
        hits = len(AI_TERMS.findall(text))
        dens = 1000 * hits / words
        if hits < 2:
            return "N", f"not AI (keyword hits {hits})", ""
        if dens < 4:
            return "review", f"AI relevance to confirm (density {dens:.1f}/1k)", ""
    return "Y", "", ""


def date_and_extract(rows, start, end, P):
    chmap = {(c["company"], c["channel"]): c for c in CH}
    inv, fc_todo, st = [], [], Counter()
    have = set()
    if P["jsonl"].exists():
        for line in open(P["jsonl"], encoding="utf-8"):
            have.add(json.loads(line)["doc_id"])
    jl = open(P["jsonl"], "a", encoding="utf-8")
    for r in rows:
        c = chmap[(r["company"], r["channel"])]
        hf = P["html"] / f"{r['doc_id']}.html.gz"
        base = dict(company=r["company"], channel=r["channel"], title=r["feed_title"], url=r["url"],
                    published_date="", ai="", genre="", note="", include_v1="", reason="", date_status="")
        if not hf.exists():
            # plain fetch failed -> Firecrawl later. Keep only if a known date is in window.
            known = r["feed_date"] or (r["url_date"] if r["url_date"] and len(r["url_date"]) == 10 and not r["url_date"].endswith("-01") else "")
            base.update(published_date=known, date_status="firecrawl-needed", include_v1="review",
                        reason="plain fetch failed; date/AI check after Firecrawl")
            fc_todo.append(base); inv.append(base); st["firecrawl-needed"] += 1
            continue
        html = gzip.decompress(hf.read_bytes()).decode("utf-8", "replace")
        text, title, lang = extract(html)
        md, mk = meta_date(html)
        vd = visible_date(text)
        cands = [("meta", md), ("url", r["url_date"] if r["url_date"] and not r["url_date"].endswith("-01") else None),
                 ("visible", vd), ("feed", r["feed_date"] or None)]
        src, val = next(((s, v) for s, v in cands if v), ("lastmod", r["lastmod"] or None))
        base["title"] = title or r["feed_title"]
        base["published_date"] = val or ""
        base["date_status"] = f"ok ({src})" if src != "lastmod" else "uncertain (lastmod only)"
        if not val or src == "lastmod":
            if val and val < start.isoformat():
                base.update(include_v1="N", reason="out of window (lastmod before start)")
                inv.append(base); st["out of window"] += 1; continue
            base.update(published_date="", include_v1="review", reason="no page date; Firecrawl for date",
                        date_status="no date; firecrawl-needed")
            fc_todo.append(base); inv.append(base); st["firecrawl-needed (no date)"] += 1; continue
        if not (start.isoformat() <= val <= end.isoformat()):
            base.update(include_v1="N", reason="out of window")
            inv.append(base); st["out of window"] += 1; continue
        if len(text.split()) < 80:
            # JS-rendered or blocked page: let Firecrawl read it
            base.update(include_v1="review", reason="plain fetch returned too little text; Firecrawl",
                        date_status=base["date_status"] + "; firecrawl-needed")
            fc_todo.append(base); inv.append(base); st["firecrawl-needed"] += 1; continue
        inc, reason, ghint = classify(c, r, base["title"], text, lang, html)
        base.update(include_v1=inc, reason=reason, genre=ghint)
        if re.search(r"\b(Jensen Huang|Jensen)\b", base["title"] + " " + text[:4000]):
            base["note"] = "jensen"
            if re.search(r"keynote|remarks|fireside|interview|speech", base["title"] + " " + u_path(r["url"]), re.I):
                base["genre"] = base["genre"] or "speech"
        inv.append(base); st[f"{base['include_v1']}"] += 1
        if base["include_v1"] in ("Y", "review") and r["doc_id"] not in have:
            (P["raw"] / f"{r['doc_id']}.md").write_text(text, encoding="utf-8")
            rec = {"doc_id": r["doc_id"], "company": r["company"], "channel": r["channel"], "genre": base["genre"],
                   "note": base["note"],
                   "title": base["title"], "url": r["url"], "date_inventory": r["feed_date"] or r["url_date"],
                   "date_meta": md, "date_meta_key": mk, "include_v1": base["include_v1"],
                   "word_count": len(text.split()), "text": text, "engine": "trafilatura",
                   "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            jl.write(json.dumps(rec, ensure_ascii=False) + "\n")
    jl.close()
    cols = ["company", "channel", "title", "url", "published_date", "ai", "genre", "note",
            "include_v1", "reason", "date_status"]
    for path, data in ((P["inv"], inv), (P["fc"], fc_todo)):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(data)
    print(f"[extract] {dict(st)}")
    per = Counter((x["company"], x["include_v1"]) for x in inv)
    for co in sorted({x["company"] for x in inv}):
        print(f"    {co:9} Y={per[(co,'Y')]:4}  review={per[(co,'review')]:4}  N={per[(co,'N')]:4}")
    return inv, fc_todo


# ---------------------------------------------------------------- main
def seed_from_pilot(P):
    """Copy pilot-scraped docs (except Meta, which had run-together words) so they are not re-fetched."""
    src = ROOT / "phase2" / "corpus_raw.jsonl"
    if not src.exists() or P["jsonl"].exists():
        return
    n = 0
    with open(P["jsonl"], "w", encoding="utf-8") as out:
        for line in open(src, encoding="utf-8"):
            d = json.loads(line)
            if d["company"] == "Meta":
                continue
            out.write(line); n += 1
            raw = ROOT / "phase2" / "raw" / f"{d['doc_id']}.md"
            if raw.exists():
                (P["raw"] / raw.name).write_text(raw.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[seed] reused {n} pilot docs (Meta re-fetched with trafilatura)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="year1")
    ap.add_argument("--start", default="2025-09-25")
    ap.add_argument("--end", default="2026-09-25")
    ap.add_argument("--stage", choices=["all", "discover", "fetch", "extract"], default="all")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests to the same host")
    ap.add_argument("--smoke", action="store_true", help="3 candidates per channel, into <root>_smoke/")
    a = ap.parse_args()
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    base = ROOT / (a.root + ("_smoke" if a.smoke else ""))
    P = dict(base=base, cand=base / "phase1" / "candidates.csv", inv=base / "phase1" / "url_inventory.csv",
             fc=base / "phase1" / "firecrawl_todo.csv", html=base / "html", raw=base / "phase2" / "raw",
             jsonl=base / "phase2" / "corpus_raw.jsonl")
    for d in (base / "phase1", P["html"], P["raw"]):
        d.mkdir(parents=True, exist_ok=True)
    print(f"window {start} .. {end}  ->  {base}")

    if a.stage in ("all", "discover") or not P["cand"].exists():
        rows = discover(a, start, end, P)
    else:
        rows = list(csv.DictReader(open(P["cand"], encoding="utf-8")))
    if a.smoke:
        k = Counter(); keep = []
        for r in rows:
            if k[(r["company"], r["channel"])] < 3:
                keep.append(r); k[(r["company"], r["channel"])] += 1
        rows = keep
    if a.stage == "discover":
        return
    if not a.smoke:
        seed_from_pilot(P)
    if a.stage in ("all", "fetch"):
        fetch_all(rows, P, a.delay)
    if a.stage in ("all", "extract", "fetch"):
        inv, fc = date_and_extract(rows, start, end, P)
        print(f"\nNext: {len(fc)} pages need Firecrawl. Run:\n"
              f"  python3 scripts/scrape.py --inv {P['fc'].relative_to(ROOT)} --out {(base/'phase2').relative_to(ROOT)} --engine firecrawl\n"
              f"Then:  python3 scripts/clean.py --root {base.relative_to(ROOT)} --start {a.start} --end {a.end}")


if __name__ == "__main__":
    main()
