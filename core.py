"""OpenAlex data collection, citation metrics, matched-baseline benchmark, graph builders.
No Streamlit dependency, so it can be tested on its own."""
from __future__ import annotations

import colorsys
import json
import math
import random
import re
import textwrap
import time
from collections import Counter, defaultdict
from functools import lru_cache

import numpy as np
import pandas as pd
import requests

API = "https://api.openalex.org"
MAX_AUTHORS = 30  # papers with more authors are skipped in author-level counts
WORK_SELECT = ("id,doi,display_name,publication_year,cited_by_count,referenced_works,"
               "authorships,type,primary_topic")
META_SELECT = "id,doi,display_name,publication_year,cited_by_count,authorships"

ORCID_RE = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dXx]")
DOI_RE = re.compile(r"10\.\d{4,9}/\S+")

SELF, CO, EXT = "Self-citation", "Co-author citation", "External citation"
KIND_COLOR = {SELF: "#ef4444", CO: "#f97316", EXT: "#3b82f6"}


# ───────────────────────── HTTP ─────────────────────────
def short_id(x):
    return x.rsplit("/", 1)[-1] if x else x


AUTH_HELP = ("OpenAlex now requires an API key (free: openalex.org/settings/api). Paste it into the sidebar "
             "field 'OpenAlex API key'. Without a key the tiny shared daily budget runs out almost immediately.")


@lru_cache(maxsize=8192)
def _get(path: str, params_json: str, api_key: str):
    params = json.loads(params_json)
    headers = {"User-Agent": "citation-trace-app"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"  # header, so the key never shows up in URLs or errors
    last = None
    for i in range(4):
        try:
            r = requests.get(f"{API}/{path}", params=params, headers=headers, timeout=40)
        except requests.RequestException as e:
            last = type(e).__name__
            time.sleep(1.5 * (i + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (401, 403):
            raise RuntimeError(f"OpenAlex rejected the request (HTTP {r.status_code}). " + AUTH_HELP)
        if r.status_code == 429:
            last = "HTTP 429 (rate limit or daily budget used up)"
            time.sleep(2 * (i + 1))
            continue
        if r.status_code in (500, 502, 503, 504):
            last = f"HTTP {r.status_code}"
            time.sleep(1.5 * (i + 1))
            continue
        raise RuntimeError(f"OpenAlex returned HTTP {r.status_code} for /{path}")
    raise RuntimeError(f"OpenAlex request failed: {last}. If this is a 429, the daily free budget may be used up "
                       "(it resets at midnight UTC); check openalex.org/settings/usage.")


def get(path, params, api_key=""):
    # The returned dict is shared with the cache: never mutate it.
    return _get(path, json.dumps(params, sort_keys=True), api_key or "")


def fetch_all(path, params, max_items, api_key=""):
    """Cursor-paginate up to max_items. Returns (items, total_count)."""
    out, cursor, total = [], "*", 0
    while cursor and len(out) < max_items:
        d = get(path, {**params, "per_page": 100, "cursor": cursor}, api_key)
        total = d["meta"].get("count", total)
        if not d["results"]:
            break
        out += d["results"]
        cursor = d["meta"].get("next_cursor")
    return out[:max_items], total


def chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ───────────────────────── Search ─────────────────────────
def _inst(a):
    li = a.get("last_known_institutions") or (
        [a["last_known_institution"]] if a.get("last_known_institution") else [])
    return ", ".join(i.get("display_name", "") for i in li if i)


def search_authors(q, api_key=""):
    m = ORCID_RE.search(q)
    if m:
        d = get("authors", {"filter": f"orcid:{m.group(0).upper()}"}, api_key)
    else:
        d = get("authors", {"search": q.strip(), "per_page": 10}, api_key)
    items = []
    for a in d["results"]:
        inst = _inst(a)
        label = (f"{a.get('display_name')} · {inst or 'unknown affiliation'} · "
                 f"{a.get('works_count', 0)} works · {a.get('cited_by_count', 0)} citations")
        if a.get("orcid"):
            label += " · " + a["orcid"].replace("https://orcid.org/", "ORCID ")
        items.append({"id": short_id(a["id"]), "name": a.get("display_name"), "label": label,
                      "works": a.get("works_count", 0)})
    return items


def search_works(q, api_key=""):
    q = q.strip()
    doi = DOI_RE.search(q)
    params = {"select": "id,display_name,publication_year,authorships,cited_by_count"}
    if doi:
        params["filter"] = f"doi:https://doi.org/{doi.group(0).rstrip('.,;)')}"
    else:
        params.update({"search": q, "per_page": 8})
    items = []
    for w in get("works", params, api_key)["results"]:
        c = compact(w, keep_refs=False)
        items.append({"id": c["id"],
                      "label": f"{c['title']} ({c['year']}) · {fmt_authors(c['authors'])} · {c['cited_by']} citations",
                      "authors": c["authors"]})
    return items


# ───────────────────────── Collection ─────────────────────────
def compact(w, keep_refs=True):
    auth = []
    for a in w.get("authorships") or []:
        au = a.get("author") or {}
        if au.get("id"):
            auth.append((short_id(au["id"]), au.get("display_name") or "?"))
    pt = w.get("primary_topic") or {}
    sf = (pt.get("subfield") or {}).get("id")
    fl = (pt.get("field") or {}).get("id")
    d = {
        "id": short_id(w["id"]),
        "title": w.get("display_name") or "(untitled)",
        "year": w.get("publication_year"),
        "cited_by": w.get("cited_by_count") or 0,
        "doi": w.get("doi"),
        "authors": auth,
        "type": w.get("type"),
        "subfield": short_id(sf) if sf else None,
        "field": short_id(fl) if fl else None,
    }
    if keep_refs:
        d["refs"] = [short_id(r) for r in w.get("referenced_works") or []]
    return d


def build_dataset(author_ids, label="", api_key="", max_works=500, max_citing=4000,
                  max_refs=3000, progress=lambda msg, frac: None):
    ids = list(author_ids)
    warnings = []

    progress("Collecting the author's works…", 0.02)
    raw, total = fetch_all("works", {"filter": "author.id:" + "|".join(ids), "select": WORK_SELECT,
                            "sort": "cited_by_count:desc"},
                           max_works, api_key)
    if total > len(raw):
        warnings.append(f"Collected {len(raw)} of {total} works (raise the limit in the sidebar).")
    my = {}
    for w in raw:
        c = compact(w)
        my[c["id"]] = c
    my_ids = set(my)
    if not my:
        raise RuntimeError("No works found for this author ID.")

    citing, truncated = {}, False
    chunks = list(chunked(sorted(my_ids), 100))
    for i, ch in enumerate(chunks):
        progress(f"Collecting citing papers… ({i + 1}/{len(chunks)})", 0.08 + 0.5 * i / len(chunks))
        room = max_citing - len(citing)
        if room <= 0:
            truncated = True
            break
        items, tot = fetch_all("works", {"filter": "cites:" + "|".join(ch), "select": WORK_SELECT},
                               room, api_key)
        if tot > len(items):
            truncated = True
        for w in items:
            c = compact(w)
            hits = [r for r in c["refs"] if r in my_ids]
            c["n_refs"] = len(c["refs"])
            del c["refs"]
            if c["id"] in citing:
                citing[c["id"]]["hits"] = sorted(set(citing[c["id"]]["hits"]) | set(hits))
            else:
                c["hits"] = hits
                citing[c["id"]] = c
    if truncated:
        warnings.append("Only part of the citing papers was collected; metrics cover the collected range only.")

    ref_cnt = Counter(r for m in my.values() for r in m["refs"] if r not in my_ids)
    wanted = [r for r, _ in ref_cnt.most_common(max_refs)]
    if len(ref_cnt) > len(wanted):
        warnings.append(f"Metadata fetched for the {len(wanted)} most-cited of {len(ref_cnt)} references.")
    refs = {m: my[m] for m in my_ids}
    chunks = list(chunked(wanted, 100))
    for i, ch in enumerate(chunks):
        progress(f"Collecting reference metadata… ({i + 1}/{len(chunks)})", 0.6 + 0.38 * i / max(len(chunks), 1))
        d = get("works", {"filter": "openalex:" + "|".join(ch), "select": META_SELECT, "per_page": 100}, api_key)
        for w in d["results"]:
            c = compact(w, keep_refs=False)
            refs[c["id"]] = c

    progress("Analyzing…", 0.99)
    return {"author_ids": ids, "label": label or ", ".join(ids), "api_key": api_key, "my": my,
            "citing": citing, "refs": refs, "warnings": warnings}


# ───────────────────────── Analysis ─────────────────────────
def fmt_authors(authors, n=3):
    names = [a[1] for a in authors]
    if len(names) <= n:
        return ", ".join(names)
    return ", ".join(names[:n]) + f" +{len(names) - n} more"


def h_index(cites):
    s = sorted(cites, reverse=True)
    return sum(1 for i, c in enumerate(s, 1) if c >= i)


def analyze(D):
    me = set(D["author_ids"])
    my, citing, refs = D["my"], D["citing"], D["refs"]
    my_ids = set(my)
    my_auth = {m: {a for a, _ in v["authors"]} for m, v in my.items()}
    coauthors = set().union(*my_auth.values()) - me if my_auth else set()

    names = {}
    for src in (my, citing, refs):
        for v in src.values():
            for a, n in v["authors"]:
                names.setdefault(a, n)

    events = []  # one event = one (citing paper -> my paper) pair
    for cid, c in citing.items():
        cauth = {a for a, _ in c["authors"]}
        for m in c["hits"]:
            if cauth & me:
                kind = SELF
            elif cauth & my_auth[m]:
                kind = CO
            else:
                kind = EXT
            events.append({"citing_id": cid, "cited_id": m, "kind": kind, "citing_year": c["year"],
                           "citing_title": c["title"], "cited_title": my[m]["title"]})
    events_df = pd.DataFrame(events, columns=["citing_id", "cited_id", "kind", "citing_year",
                                              "citing_title", "cited_title"])
    kind_map = defaultdict(dict)
    for e in events:
        kind_map[e["cited_id"]][e["citing_id"]] = e["kind"]

    nonself = [e for e in events if e["kind"] != SELF]
    inc = Counter()
    for e in nonself:
        c = citing[e["citing_id"]]
        if len(c["authors"]) > MAX_AUTHORS:
            continue
        for a in {a for a, _ in c["authors"]} - me:
            inc[a] += 1

    ref_cnt = Counter(r for m in my.values() for r in m["refs"])
    out = Counter()
    for m, v in my.items():
        for r in v["refs"]:
            if r in my_ids:
                continue
            rv = refs.get(r)
            if not rv or len(rv["authors"]) > MAX_AUTHORS:
                continue
            for a in {a for a, _ in rv["authors"]} - me:
                out[a] += 1

    cols_r = ["Author", "id", "Cites me", "I cite", "Mutual (min)", "Co-author"]
    recip = [{"Author": names.get(a, a), "id": a, "Cites me": inc[a], "I cite": out[a],
              "Mutual (min)": min(inc[a], out[a]), "Co-author": a in coauthors}
             for a in set(inc) & set(out)]
    recip_df = pd.DataFrame(recip, columns=cols_r)
    if not recip_df.empty:
        recip_df = recip_df.sort_values(["Mutual (min)", "Cites me"], ascending=False).reset_index(drop=True)
    mutual_ids = set(recip_df.loc[recip_df["Mutual (min)"] >= 2, "id"]) if not recip_df.empty else set()

    def top_df(counter, other):
        rows = [{"Author": names.get(a, a), "id": a, "Count": n, "Opposite direction": other.get(a, 0),
                 "Co-author": a in coauthors} for a, n in counter.most_common(30)]
        return pd.DataFrame(rows, columns=["Author", "id", "Count", "Opposite direction", "Co-author"])

    def top_share(n):
        if not nonself:
            return 0.0
        top = {a for a, _ in inc.most_common(n)}
        hit = sum(1 for e in nonself
                  if len(citing[e["citing_id"]]["authors"]) <= MAX_AUTHORS
                  and top & {a for a, _ in citing[e["citing_id"]]["authors"]})
        return hit / len(nonself)

    ev_by_m = defaultdict(list)
    for e in events:
        ev_by_m[e["cited_id"]].append(e)
    rows = []
    for m, v in my.items():
        k = Counter(e["kind"] for e in ev_by_m[m])
        n = len(ev_by_m[m])
        rows.append({
            "id": m, "Year": v["year"], "Title": v["title"],
            "Cited by (OpenAlex)": v["cited_by"], "Citations collected": n,
            "Self": k[SELF], "Co-author": k[CO], "External": k[EXT],
            "Self-citation %": round(100 * k[SELF] / n, 1) if n else 0.0,
            "References": len(v["refs"]),
            "Cites to my other works": sum(1 for r in v["refs"] if r in my_ids and r != m),
            "Link": "https://openalex.org/" + m,
        })
    papers_df = pd.DataFrame(rows).sort_values("Cited by (OpenAlex)", ascending=False).reset_index(drop=True)

    heavy = []
    for cid, c in citing.items():
        kinds = {kind_map[m][cid] for m in c["hits"]}
        if SELF in kinds or len(c["hits"]) < 2:
            continue
        heavy.append({"Year": c["year"], "Title": c["title"], "Authors": fmt_authors(c["authors"]),
                      "My papers cited": len(c["hits"]), "References": c["n_refs"],
                      "Share of its references %": round(100 * len(c["hits"]) / max(c["n_refs"], 1), 1),
                      "Link": "https://openalex.org/" + cid})
    heavy_df = pd.DataFrame(heavy, columns=["Year", "Title", "Authors", "My papers cited", "References",
                                          "Share of its references %", "Link"])
    if not heavy_df.empty:
        heavy_df = heavy_df.sort_values(["My papers cited", "Share of its references %"],
                                        ascending=False).reset_index(drop=True)

    n_ev = len(events)
    kc = Counter(e["kind"] for e in events)
    summary = {
        "works": len(my),
        "cited_total": sum(v["cited_by"] for v in my.values()),
        "h_index": h_index([v["cited_by"] for v in my.values()]),
        "i10": sum(1 for v in my.values() if v["cited_by"] >= 10),
        "citing_works": len(citing),
        "events": n_ev,
        "self_pct": 100 * kc[SELF] / n_ev if n_ev else 0,
        "co_pct": 100 * kc[CO] / n_ev if n_ev else 0,
        "ext_pct": 100 * kc[EXT] / n_ev if n_ev else 0,
        "unique_citers": len(inc),
        "top1_share": 100 * top_share(1),
        "top5_share": 100 * top_share(5),
        "mutual_n": len(mutual_ids),
        "refs_total": sum(len(v["refs"]) for v in my.values()),
        "refs_resolved": sum(1 for v in my.values() for r in v["refs"] if r in refs),
    }
    return {"summary": summary, "papers_df": papers_df, "events_df": events_df, "recip_df": recip_df,
            "inc_df": top_df(inc, out), "out_df": top_df(out, inc), "heavy_df": heavy_df,
            "kind_map": dict(kind_map), "inc": inc, "out": out, "names": names,
            "coauthors": coauthors, "mutual_ids": mutual_ids, "ref_cnt": ref_cnt}


# ───────────────────────── Matched-baseline benchmark ─────────────────────────
MIN_CITING = 8   # a paper needs at least this many citing papers to be measured
MIN_EXT = 5      # ... and this many non-shared-author citers for the concentration metrics
METRICS = {
    "shared": "Shared-author citations %",
    "top": "Top-citer concentration %",
    "recip": "Reciprocal citations %",
}
METRIC_HELP = {
    "shared": "Share of citing papers that share at least one author with the cited paper (author self-citation).",
    "top": "Among citing papers with no shared author: share that include the single most frequent citing author.",
    "recip": "Among citing papers with no shared author: share that include an author whose work the cited paper itself cites.",
}


def paper_metrics(cited_authors, citing_sets, ref_authors):
    """Three per-paper metrics (all in %). Returns None if too few citing papers."""
    cl = [a for a in citing_sets if 0 < len(a) <= MAX_AUTHORS]
    n = len(cl)
    if n < MIN_CITING:
        return None
    shared = [bool(a & cited_authors) for a in cl]
    ext = [a for a, s in zip(cl, shared) if not s]
    res = {"shared": 100 * sum(shared) / n, "top": None, "recip": None}
    if len(ext) >= MIN_EXT:
        cnt = Counter(x for a in ext for x in a)
        res["top"] = 100 * cnt.most_common(1)[0][1] / len(ext)
        ra = ref_authors - cited_authors
        res["recip"] = 100 * sum(1 for a in ext if a & ra) / len(ext)
    return res


def _ref_author_set(refs_meta, ref_ids):
    s = set()
    for r in ref_ids:
        v = refs_meta.get(r)
        if v and 0 < len(v["authors"]) <= MAX_AUTHORS:
            s |= {a for a, _ in v["authors"]}
    return s


def target_metrics(D, m):
    cited = {a for a, _ in D["my"][m]["authors"]}
    sets = [{a for a, _ in c["authors"]} for c in D["citing"].values() if m in c["hits"]]
    return paper_metrics(cited, sets, _ref_author_set(D["refs"], D["my"][m]["refs"]))


def baseline_metrics(b, api_key="", cap=300):
    items, _ = fetch_all("works", {"filter": f"cites:{b['id']}", "select": "id,authorships"}, cap, api_key)
    sets = [{a for a, _ in compact(w, keep_refs=False)["authors"]} for w in items]
    refs_meta = {}
    for ch in chunked(b["refs"][:200], 100):
        d = get("works", {"filter": "openalex:" + "|".join(ch), "select": "id,authorships", "per_page": 100}, api_key)
        for w in d["results"]:
            c = compact(w, keep_refs=False)
            refs_meta[c["id"]] = c
    return paper_metrics({a for a, _ in b["authors"]}, sets, _ref_author_set(refs_meta, b["refs"][:200]))


def pick_baseline(t, exclude_ids, me, api_key, k, seed):
    """Random papers of the same type / year / subfield with a similar citation count."""
    c = t["cited_by"]
    lo, hi = max(int(c * 0.5) - 1, 0), int(math.ceil(c * 2)) + 1
    base = f"publication_year:{t['year']},cited_by_count:>{lo},cited_by_count:<{hi}"
    if t.get("type"):
        base += f",type:{t['type']}"
    scopes = []
    if t.get("subfield"):
        scopes.append(f"primary_topic.subfield.id:{t['subfield']}")
    if t.get("field"):
        scopes.append(f"primary_topic.field.id:{t['field']}")
    for scope in scopes:
        try:
            d = get("works", {"filter": f"{scope},{base}", "sample": k * 4, "seed": seed,
                              "per_page": min(k * 4, 100), "select": WORK_SELECT}, api_key)
        except Exception:
            continue
        picked = []
        for w in d["results"]:
            b = compact(w)
            if b["id"] in exclude_ids or {a for a, _ in b["authors"]} & me or not b["refs"]:
                continue
            picked.append(b)
            if len(picked) >= k:
                break
        if len(picked) >= 2:
            return picked
    return []


def _pct_rank(v, base):
    less = sum(1 for x in base if x < v)
    eq = sum(1 for x in base if x == v)
    return 100 * (less + 0.5 * eq) / len(base)


def _reading(n, lo, hi):
    if n < 5:
        return "Too few papers to judge"
    if lo > 0:
        return "Above baseline"
    if hi < 0:
        return "Below baseline"
    return "Not distinguishable from baseline"


def collect_benchmark(D, n_target=20, k_base=3, min_cites=10, max_cites=300, seed=1, api_key="",
                      progress=lambda msg, frac: None):
    """Network-heavy part: sample your papers, draw matched baselines, measure everything."""
    me, my = set(D["author_ids"]), D["my"]
    eligible = [m for m, v in my.items()
                if min_cites <= v["cited_by"] <= max_cites and v["year"] and (v["subfield"] or v["field"])]
    rng = random.Random(seed)
    sample = rng.sample(eligible, min(n_target, len(eligible)))

    per_work, used_baseline = [], set()
    for i, m in enumerate(sample):
        progress(f"Benchmarking paper {i + 1}/{len(sample)}…", 0.02 + 0.96 * i / max(len(sample), 1))
        tm = target_metrics(D, m)
        if tm is None:
            continue
        bl = pick_baseline(my[m], set(my) | used_baseline, me, api_key, k_base, seed + i)
        bms = [bm for bm in (baseline_metrics(b, api_key) for b in bl) if bm]
        used_baseline |= {b["id"] for b in bl}
        if len(bms) < 2:
            continue
        per_work.append({"id": m, "t": tm, "b": bms})
    return per_work, len(eligible), len(sample), len(used_baseline)


def summarize_benchmark(D, per_work, n_eligible, n_sampled, n_baseline, seed=1):
    """Pure computation: per-paper table, long table (for charts), summary with bootstrap CI."""
    my = D["my"]
    rows, long_rows, summ = [], [], []
    for w in per_work:
        v = my[w["id"]]
        row = {"Year": v["year"], "Title": v["title"], "Cited by": v["cited_by"], "Baseline papers": len(w["b"])}
        for key, name in METRICS.items():
            bv = [b[key] for b in w["b"] if b[key] is not None]
            tv = w["t"][key]
            short = name.replace(" %", "")
            base_mean = round(sum(bv) / len(bv), 1) if bv else None
            pct = round(_pct_rank(tv, bv)) if (tv is not None and len(bv) >= 2) else None
            row[f"{short}: yours"] = None if tv is None else round(tv, 1)
            row[f"{short}: baseline"] = base_mean
            row[f"{short}: percentile"] = pct
            if tv is not None and base_mean is not None:
                long_rows.append({"Metric": name, "Title": core_trunc(v["title"], 80), "Year": v["year"],
                                  "Yours": round(tv, 1), "Baseline": base_mean,
                                  "Difference": round(tv - base_mean, 1), "Percentile": pct})
        rows.append(row)

    np_rng = np.random.default_rng(seed)
    for key, name in METRICS.items():
        pairs = []
        for w in per_work:
            bv = [b[key] for b in w["b"] if b[key] is not None]
            if w["t"][key] is not None and len(bv) >= 2:
                pairs.append((w["t"][key], sum(bv) / len(bv), _pct_rank(w["t"][key], bv)))
        if not pairs:
            summ.append({"Metric": name, "Papers compared": 0, "Your mean": None, "Baseline mean": None,
                         "Mean difference (pp)": None, "95% CI low": None, "95% CI high": None,
                         "Mean percentile": None, "Reading": "Too few papers to judge"})
            continue
        t_arr, b_arr, p_arr = (np.array(x) for x in zip(*pairs))
        d = t_arr - b_arr
        boots = [d[np_rng.integers(0, len(d), len(d))].mean() for _ in range(2000)]
        lo, hi = np.percentile(boots, [2.5, 97.5])
        summ.append({"Metric": name, "Papers compared": len(pairs), "Your mean": round(t_arr.mean(), 1),
                     "Baseline mean": round(b_arr.mean(), 1), "Mean difference (pp)": round(d.mean(), 1),
                     "95% CI low": round(lo, 1), "95% CI high": round(hi, 1),
                     "Mean percentile": round(p_arr.mean()), "Reading": _reading(len(pairs), lo, hi)})

    long_cols = ["Metric", "Title", "Year", "Yours", "Baseline", "Difference", "Percentile"]
    return {"summary_df": pd.DataFrame(summ), "papers_df": pd.DataFrame(rows),
            "long_df": pd.DataFrame(long_rows, columns=long_cols),
            "n_eligible": n_eligible, "n_sampled": n_sampled, "n_used": len(per_work),
            "n_baseline": n_baseline}


def run_benchmark(D, n_target=20, k_base=3, min_cites=10, max_cites=300, seed=1, api_key="",
                  progress=lambda msg, frac: None):
    per_work, ne, ns, nb = collect_benchmark(D, n_target, k_base, min_cites, max_cites, seed, api_key, progress)
    return summarize_benchmark(D, per_work, ne, ns, nb, seed)


# ───────────────────────── Graphs ─────────────────────────
def trunc(s, n):
    return s if len(s) <= n else s[:n - 1] + "…"


core_trunc = trunc


def _tip(v, kind=None):
    t = f"{textwrap.fill(v['title'], 70)}\n{fmt_authors(v['authors'], 5)}\n{v['year']} · cited by {v['cited_by']}"
    return t + (f"\n[{kind}]" if kind else "")


def node_label(v, style="short"):
    """Short node label: 'Surname et al., 2021' (+ a short title unless style == 'compact'). Full title is in the tooltip."""
    au = v["authors"][0][1].split()[-1] if v["authors"] else "?"
    if len(v["authors"]) > 1:
        au += " et al."
    head = f"{au}, {v['year']}"
    return head if style == "compact" else f"{head}\n{trunc(v['title'], 34)}"


def paper_graph(D, A, m, k=15, label_style="short"):
    """Left: papers citing this one -> center: selected paper -> right: papers it cites."""
    my, citing, refs = D["my"], D["citing"], D["refs"]
    my_ids, mutual = set(my), A["mutual_ids"]
    kmap = A["kind_map"].get(m, {})
    box = {"shape": "box", "widthConstraint": {"maximum": 190}, "margin": 6}

    nodes = [{"id": "center", "label": trunc(my[m]["title"], 60), "title": _tip(my[m]), "level": 1,
              "color": {"background": "#fbbf24", "border": "#b45309"}, "font": {"size": 15, "color": "#111"},
              "shape": "box", "widthConstraint": {"maximum": 240}, "margin": 8}]
    edges = []

    cl = sorted(((cid, c) for cid, c in citing.items() if m in c["hits"]),
                key=lambda x: (-len(x[1]["hits"]), -x[1]["cited_by"]))
    for cid, c in cl[:k]:
        kind = kmap[cid]
        mut = any(a in mutual for a, _ in c["authors"])
        col = KIND_COLOR[kind]
        nodes.append({"id": "c_" + cid, "label": node_label(c, label_style), "title": _tip(c, kind), "level": 0,
                      "color": {"background": col, "border": "#7c3aed" if mut else col},
                      "borderWidth": 4 if mut else 1, "font": {"color": "#fff", "size": 11}, **box})
        edges.append({"from": "c_" + cid, "to": "center"})

    rl = sorted((refs[r] for r in my[m]["refs"] if r in refs),
                key=lambda v: (v["id"] not in my_ids, -v["cited_by"]))
    for v in rl[:k]:
        mine = v["id"] in my_ids
        mut = any(a in mutual for a, _ in v["authors"])
        col = "#10b981" if mine else "#9ca3af"
        nodes.append({"id": "r_" + v["id"], "label": node_label(v, label_style),
                      "title": _tip(v, "my other work" if mine else None), "level": 2,
                      "color": {"background": col, "border": "#7c3aed" if mut else col},
                      "borderWidth": 4 if mut else 1, "font": {"color": "#fff" if mine else "#111", "size": 11},
                      **box})
        edges.append({"from": "center", "to": "r_" + v["id"]})
    return nodes, edges, max(len(cl) - k, 0), max(len(rl) - k, 0)


def author_graph(D, A, k=15, layout="force", hide_coauthors=False):
    """Author-level network. layout: 'force' (physics), 'radial' (rings) or 'lr' (citers -> me -> cited)."""
    inc, out, names, coauth = A["inc"], A["out"], A["names"], A["coauthors"]
    keep = (lambda a: a not in coauth) if hide_coauthors else (lambda a: True)
    top_in = [a for a, _ in inc.most_common() if keep(a)][:k]
    top_out = [a for a, _ in out.most_common() if keep(a)][:k]
    ids = list(dict.fromkeys(top_in + top_out))

    kinds = {}
    for a in ids:
        i, o = inc.get(a, 0), out.get(a, 0)
        kinds[a] = "both" if (i and o) else ("in" if i else "out")
    groups = {g: [a for a in ids if kinds[a] == g] for g in ("in", "out", "both")}

    pos = {}
    if layout == "radial":
        def ring(items, radius, a0, a1):
            n = len(items)
            for j, a in enumerate(items):
                t = a0 + (a1 - a0) * (j + 0.5) / max(n, 1)
                pos[a] = (radius * math.cos(math.radians(t)), radius * math.sin(math.radians(t)))
        ring(groups["both"], 170, 0, 360)
        ring(groups["in"], max(340, 16 * len(groups["in"])), 100, 260)
        ring(groups["out"], max(340, 16 * len(groups["out"])), -80, 80)

    nodes = [{"id": "me", "label": trunc(D["label"], 30), "shape": "star", "size": 32,
              "color": {"background": "#fbbf24", "border": "#b45309"}, "font": {"size": 16}, "level": 1,
              **({"x": 0, "y": 0} if layout == "radial" else {})}]
    edges = []
    palette = {"in": "#3b82f6", "out": "#10b981", "both": "#ef4444"}
    for a in ids:
        i, o = inc.get(a, 0), out.get(a, 0)
        is_co = a in coauth
        n = {"id": a, "label": ("◆ " if is_co else "") + trunc(names.get(a, a), 22),
             "shape": "diamond" if is_co else "dot", "size": 10 + 4 * math.sqrt(i + o),
             "color": {"background": palette[kinds[a]], "border": "#111" if is_co else palette[kinds[a]]},
             "borderWidth": 3 if is_co else 1,
             "title": f"{names.get(a, a)}\ncites me {i}x · I cite them {o}x" + ("\nco-author" if is_co else ""),
             "level": 2 if kinds[a] == "out" else 0}
        if layout == "radial":
            n["x"], n["y"] = pos[a]
        nodes.append(n)
        if i:
            edges.append({"from": a, "to": "me", "label": str(i), "width": 1 + math.log2(i + 1), "color": "#3b82f6"})
        if o:
            edges.append({"from": "me", "to": a, "label": str(o), "width": 1 + math.log2(o + 1), "color": "#10b981"})
    return nodes, edges


def internal_graph(D, hide_isolated=True):
    my = D["my"]
    my_ids = set(my)
    edges, linked = [], set()
    for m, v in my.items():
        for r in v["refs"]:
            if r in my_ids and r != m:
                edges.append({"from": m, "to": r})
                linked |= {m, r}
    years = [v["year"] for v in my.values() if v["year"]]
    y0, y1 = (min(years), max(years)) if years else (0, 1)
    nodes = []
    for m, v in my.items():
        if hide_isolated and m not in linked:
            continue
        t = ((v["year"] or y0) - y0) / max(y1 - y0, 1)
        r, g, b = colorsys.hls_to_rgb(0.58, 0.8 - 0.5 * t, 0.7)
        nodes.append({"id": m, "label": str(v["year"]), "title": _tip(v), "shape": "dot",
                      "size": 8 + 2.5 * math.sqrt(v["cited_by"]),
                      "color": "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))})
    return nodes, edges
