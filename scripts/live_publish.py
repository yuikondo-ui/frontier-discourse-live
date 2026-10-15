#!/usr/bin/env python3
"""Validate work/coded.jsonl against work/to_code.jsonl and write one markdown file per
item into the data branch checkout: items/<layer>/<YYYY-MM>/<id>.md

The markdown body is the harmonized, masked topic description (what Latent Lab embeds);
metadata lives in YAML front matter (date drives the timeline, layer/group for colour).

Usage:
    python3 scripts/live_publish.py --data ../data --check   # validate only
    python3 scripts/live_publish.py --data ../data           # validate + write files
Also used once to seed the map from the #713 CSV:
    python3 scripts/live_publish.py --data ../data --seed-csv latentlab_four_layers_harmonized.csv
"""
import argparse, csv, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"

BANNED = re.compile(r"\b(post|article|blog|tweet|thread|bill|notice|hearing|testimony|press release|announcement|"
                    r"newsletter|op-ed|column|podcast|episode|interview|the author|this document)\b", re.I)
NAMES = re.compile(r"\b(OpenAI|Anthropic|Google|DeepMind|Meta|Microsoft|Amazon|AWS|NVIDIA|Nvidia|xAI|ChatGPT|Claude|"
                   r"Gemini|Copilot|Grok|Llama|Sora|Alexa|Musk|Altman|Amodei|Hassabis|Nadella|Zuckerberg|Pichai|Jassy|"
                   r"Trump|Newsom|White House|Pentagon|Congress|Commission)\b")
FRAMES = {"capability", "benefit", "risk", "governance", "economy", "science"}
COMPANIES = {"OpenAI", "Anthropic", "Google", "Meta", "Microsoft", "Amazon", "xAI", "NVIDIA", "Other", ""}
GROUP = {"news": "News", "policy": "Policy"}


def problems(c):
    out = []
    t = c.get("topic_desc", "")
    n = len(t.split())
    if c.get("ai_relevant") not in ("yes", "partial", "no"):
        out.append("ai_relevant must be yes/partial/no")
    if c.get("ai_relevant") == "no":
        return out
    if not 20 <= n <= 55:
        out.append(f"topic_desc has {n} words (want 20-55)")
    for rx, what in ((BANNED, "genre word"), (NAMES, "unmasked name")):
        hits = sorted({m.group(0) for m in rx.finditer(t)})
        if hits:
            out.append(f"{what}: {', '.join(hits)}")
    tags = c.get("topic_tags") or []
    if not 3 <= len(tags) <= 5:
        out.append(f"{len(tags)} topic_tags (want 3-5)")
    if c.get("frame") not in FRAMES:
        out.append(f"bad frame {c.get('frame')!r}")
    if c.get("focal_company", "") not in COMPANIES:
        out.append(f"bad focal_company {c.get('focal_company')!r}")
    for k in ("vet_valence", "vet_effectiveness", "vet_trajectory"):
        if not isinstance(c.get(k), int) or not -2 <= c[k] <= 2:
            out.append(f"{k} must be an integer -2..2")
    return out


def yaml_str(v):
    return json.dumps("" if v is None else str(v), ensure_ascii=False)  # JSON strings are valid YAML


def write_item(data, d):
    layer = d["layer"]
    path = Path(data) / "items" / layer / d["date"][:7] / f"{d['id']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    tags = d["topic_tags"] if isinstance(d["topic_tags"], list) else [x for x in d["topic_tags"].split("|") if x]
    meta = [("title", d["title"]), ("date", d["date"]), ("layer", layer),
            ("group", GROUP.get(layer, d["source"])), ("source", d["source"]),
            ("jurisdiction", d.get("jurisdiction", "")), ("frame", d.get("frame", "")),
            ("focal_company", d.get("focal_company", "")), ("main_voice", d.get("main_voice", "")),
            ("doc_type", d.get("doc_type", "")), ("vet_valence", d.get("vet_valence", 0)),
            ("vet_effectiveness", d.get("vet_effectiveness", 0)), ("vet_trajectory", d.get("vet_trajectory", 0)),
            ("ai_relevant", d.get("ai_relevant", "")), ("url", d["url"])]
    lines = ["---"]
    for k, v in meta:
        lines.append(f"{k}: {v}" if isinstance(v, int) else f"{k}: {yaml_str(v)}")
    lines.append("topic_tags: [" + ", ".join(yaml_str(t) for t in tags) + "]")
    lines += ["---", "", d["topic_desc"].strip(), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def seed(a):
    n = 0
    for r in csv.DictReader(open(a.seed_csv, encoding="utf-8")):
        if r["layer"] not in ("company", "news", "policy"):
            continue
        r["topic_desc"] = r["text"]
        for k in ("vet_valence", "vet_effectiveness", "vet_trajectory"):
            r[k] = int(r[k] or 0)
        write_item(a.data, r); n += 1
    print(f"[seed] wrote {n} items")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--seed-csv")
    a = ap.parse_args()
    if a.seed_csv:
        return seed(a)
    src = {json.loads(l)["id"]: json.loads(l) for l in open(WORK / "to_code.jsonl", encoding="utf-8") if l.strip()}
    coded = {}
    bad = 0
    if (WORK / "coded.jsonl").exists():
        for i, l in enumerate(open(WORK / "coded.jsonl", encoding="utf-8"), 1):
            if not l.strip():
                continue
            try:
                c = json.loads(l)
            except json.JSONDecodeError as e:
                print(f"line {i}: invalid JSON ({e})"); bad += 1; continue
            coded[c.get("id")] = c
    missing = [i for i in src if i not in coded]
    extra = [i for i in coded if i not in src]
    for i in missing:
        print(f"{i}: missing from coded.jsonl"); bad += 1
    for i in extra:
        print(f"{i}: not in to_code.jsonl"); bad += 1
    for i, c in coded.items():
        if i in src:
            for p in problems(c):
                print(f"{i}: {p}"); bad += 1
    if bad:
        print(f"[check] {bad} problem(s). Fix work/coded.jsonl and re-run."); sys.exit(1)
    keep = [i for i, c in coded.items() if c["ai_relevant"] != "no"]
    print(f"[check] ok: {len(coded)} coded, {len(keep)} to publish, {len(coded) - len(keep)} not AI-relevant")
    if a.check:
        return
    for i in keep:
        d = {**src[i], **coded[i]}
        d.pop("text", None)
        print(f"  + {write_item(a.data, d).relative_to(a.data)}")
    print(f"[publish] {len(keep)} items written")


if __name__ == "__main__":
    main()
