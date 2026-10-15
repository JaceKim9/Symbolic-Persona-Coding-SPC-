"""Citation Trace: trace one researcher's citation network with OpenAlex data."""
import json

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import core
import demo

# ── Branding (edit freely) ──
BRAND = "Created by jacekim9"
SERIES = "SPC Research Series · Symbolic Persona Coding"
BLURB = ("Built as a companion tool for the SPC Research Series: a way to see how a body of research is cited, "
         "extended, and how that compares with its field.")

QUICK = {"max_works": 100, "max_citing": 800, "max_refs": 500}

st.set_page_config(page_title="Citation Trace", page_icon="🕸️", layout="wide")

LINK = st.column_config.LinkColumn("Link", display_text="Open")
PCT = st.column_config.NumberColumn(format="%.1f%%")

READING_COLOR = {"Above baseline": "#f97316", "Below baseline": "#3b82f6",
                 "Not distinguishable from baseline": "#6b7280", "Too few papers to judge": "#d1d5db"}
READING_ICON = {"Above baseline": "🔺", "Below baseline": "🔻",
                "Not distinguishable from baseline": "✅", "Too few papers to judge": "❔"}
METRIC_KEY = {v: k for k, v in core.METRICS.items()}


# ───────────────────────── Helpers ─────────────────────────


def show_df(data, **kw):
    """st.dataframe that works before and after `use_container_width` was replaced by `width`."""
    try:
        st.dataframe(data, width="stretch", **kw)
    except Exception:
        st.dataframe(data, use_container_width=True, **kw)


def show_chart(chart, target=st):
    try:
        target.altair_chart(chart, width="stretch")
    except Exception:
        target.altair_chart(chart, use_container_width=True)


def show_html(html, height):
    """st.iframe on new Streamlit (components.html is removed there), components.html on older versions."""
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:
        components.html(html, height=height, scrolling=False)


def vis(nodes, edges, options, height=640):
    """Render a vis-network graph inside an iframe."""
    def dump(o):
        return json.dumps(o, ensure_ascii=False).replace("</", "<\\/")

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>html,body{{margin:0}}#g{{width:100%;height:{height}px;border:1px solid #ddd;border-radius:8px;background:#fff}}
.vis-tooltip{{white-space:pre-wrap;max-width:420px;font:12px sans-serif;padding:6px 8px;border:1px solid #bbb;
background:#fffef5;border-radius:6px}}</style>
</head><body><div id="g"></div><script>
const nodes = new vis.DataSet({dump(nodes)});
const edges = new vis.DataSet({dump(edges)});
new vis.Network(document.getElementById('g'), {{nodes, edges}}, {dump(options)});
</script></body></html>"""
    show_html(html, height + 10)


FORCE = {
    "physics": {"solver": "forceAtlas2Based", "stabilization": {"iterations": 200}},
    "edges": {"arrows": "to", "smooth": {"type": "curvedCW", "roundness": 0.15}},
    "interaction": {"hover": True, "tooltipDelay": 80},
}
RADIAL = {
    "physics": {"enabled": False},
    "edges": {"arrows": "to", "smooth": {"type": "curvedCW", "roundness": 0.12}},
    "interaction": {"hover": True, "tooltipDelay": 80},
}
TREE = {
    "layout": {"hierarchical": {"enabled": True, "direction": "LR", "sortMethod": "directed",
                                "levelSeparation": 300, "nodeSpacing": 40}},
    "physics": {"enabled": False},
    "edges": {"arrows": "to", "smooth": {"type": "cubicBezier"}, "color": {"color": "#9ca3af"}},
    "interaction": {"hover": True, "tooltipDelay": 80, "dragNodes": False},
}
LAYOUTS = {"Force-directed (clusters form by themselves)": ("force", FORCE),
           "Radial rings (mutual = inner ring)": ("radial", RADIAL),
           "Left → right (citers → me → cited)": ("lr", TREE)}


def csv_button(label, df, name):
    st.download_button(label, df.to_csv(index=False).encode("utf-8-sig"), file_name=name, mime="text/csv")


def hide_id(df):
    return df.drop(columns=["id"], errors="ignore")


def footer():
    st.divider()
    st.caption(f"**{BRAND}** · {SERIES}  \n{BLURB}")


def load_demo():
    D, A, B = demo.make_demo()
    st.session_state.update(D=D, A=A, B=B)
    st.session_state.pop("search", None)


# ── benchmark visuals ──
def bench_cards(B):
    sdf = B["summary_df"]
    for col, (_, r) in zip(st.columns(len(sdf)), sdf.iterrows()):
        key = METRIC_KEY.get(r["Metric"])
        if pd.isna(r["Your mean"]):
            col.metric(r["Metric"], "n/a")
            col.caption("❔ Too few papers to judge")
            continue
        col.metric(r["Metric"], f"{r['Your mean']:.1f}%", delta=f"{r['Mean difference (pp)']:+.1f} pp vs matched peers",
                   delta_color="off", help=core.METRIC_HELP.get(key))
        col.caption(f"{READING_ICON[r['Reading']]} **{r['Reading']}**  \n"
                    f"95% CI {r['95% CI low']:+.1f} to {r['95% CI high']:+.1f} pp · "
                    f"mean percentile {r['Mean percentile']:.0f} · {r['Papers compared']} papers")


def forest_chart(sdf):
    d = sdf.dropna(subset=["Mean difference (pp)"]).rename(columns={
        "Metric": "metric", "Mean difference (pp)": "diff", "95% CI low": "lo", "95% CI high": "hi", "Reading": "reading"})
    if d.empty:
        return
    xt = "Yours − matched peers (percentage points)"
    color = alt.Color("reading:N", legend=alt.Legend(title=None, orient="bottom"),
                      scale=alt.Scale(domain=list(READING_COLOR), range=list(READING_COLOR.values())))
    base = alt.Chart(d).encode(y=alt.Y("metric:N", title=None, sort=None))
    rule = base.mark_rule(size=4).encode(x=alt.X("lo:Q", title=xt), x2="hi:Q", color=color)
    pts = base.mark_point(filled=True, size=160, opacity=1).encode(
        x=alt.X("diff:Q", title=xt), color=color,
        tooltip=[alt.Tooltip("metric:N"), alt.Tooltip("diff:Q", title="mean diff"), alt.Tooltip("lo:Q", title="CI low"),
                 alt.Tooltip("hi:Q", title="CI high")])
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(strokeDash=[5, 4], color="#888").encode(x=alt.X("x:Q", title=xt))
    show_chart((zero + rule + pts).properties(height=170))
    st.caption("Dot = average difference; bar = bootstrap 95% interval. A bar crossing the dashed zero line means "
               "'not distinguishable from comparable papers'.")


def scatter_chart(long_df, metric):
    d = long_df[long_df["Metric"] == metric].dropna(subset=["Yours", "Baseline"])
    if d.empty:
        return None
    mx = float(max(d["Yours"].max(), d["Baseline"].max(), 1)) * 1.08
    dom = alt.Scale(domain=[0, mx])
    xe = alt.X("Baseline:Q", title="Matched peers (mean %)", scale=dom)
    ye = alt.Y("Yours:Q", title="Your paper (%)", scale=dom)
    line = alt.Chart(pd.DataFrame({"Baseline": [0, mx], "Yours": [0, mx]})).mark_line(
        strokeDash=[4, 4], color="#888").encode(x=xe, y=ye)
    pts = alt.Chart(d).mark_circle(size=90, opacity=0.8, color="#3b82f6").encode(
        x=xe, y=ye, tooltip=["Title", "Year", "Yours", "Baseline", "Percentile"])
    return (line + pts).properties(height=270, title=metric)


# ───────────────────────── Sidebar ─────────────────────────
with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("OpenAlex API key", type="password",
                            help="Required by OpenAlex since 2026. Free at openalex.org/settings/api (about $1 of usage per day).")
    if not api_key:
        try:
            api_key = st.secrets.get("OPENALEX_API_KEY", "")
        except Exception:
            api_key = ""
        if api_key:
            st.caption("Using the key from this app's secrets.")
    st.markdown("**Full analysis limits**")
    max_works = st.slider("Max works", 50, 1000, 500, 50)
    max_citing = st.slider("Max citing papers to collect", 500, 10000, 4000, 500)
    max_refs = st.slider("Max reference records to collect", 500, 6000, 3000, 500)
    st.caption("'Quick preview' ignores these and uses 100 works / 800 citing papers / 500 references.")
    st.button("Load synthetic demo", on_click=load_demo, key="demo_side", help="No key needed. Fake data.")

# ───────────────────────── Header / search ─────────────────────────
st.title("🕸️ Citation Trace")
st.caption("Start from an ORCID, an author name, or a single paper. See the author's full publication list and, "
           "for each paper, who cites it, what it cites, and how that compares with the field. Data: OpenAlex.  \n"
           f"{BRAND} · {SERIES}")

if not api_key:
    st.error("**No OpenAlex API key found.** OpenAlex now requires a key, and without one almost every request fails. "
             "Paste a free key into the sidebar (openalex.org/settings/api), or explore the app first with "
             "synthetic demo data.")
    st.button("▶ Load synthetic demo (no key needed)", on_click=load_demo, key="demo_main", type="primary")

with st.form("search_form"):
    mode = st.radio("Search by", ["ORCID", "Author name", "Paper title / DOI"], horizontal=True)
    q = st.text_input("Query", placeholder="0000-0002-1825-0097  /  Jane Doe  /  paper title  /  10.1038/nature12373")
    submitted = st.form_submit_button("Search", type="primary")

if submitted and q.strip():
    for key in ("D", "A", "B"):
        st.session_state.pop(key, None)
    try:
        with st.spinner("Searching…"):
            items = core.search_works(q, api_key) if mode == "Paper title / DOI" else core.search_authors(q, api_key)
        st.session_state["search"] = {"mode": mode, "items": items}
    except Exception as e:
        st.session_state.pop("search", None)
        st.error(f"Search failed: {e}")

S = st.session_state.get("search")
chosen = []
if S:
    if not S["items"]:
        st.warning("No results. Check the spelling or ORCID format, or try another search mode.")
    elif S["mode"] == "Paper title / DOI":
        pi = st.selectbox("Select the paper", range(len(S["items"])), format_func=lambda i: S["items"][i]["label"])
        authors = S["items"][pi]["authors"]
        ai = st.multiselect("Select the author(s) to analyze", range(len(authors)),
                            default=[0] if authors else [], format_func=lambda i: authors[i][1])
        chosen = [{"id": authors[i][0], "name": authors[i][1]} for i in ai]
    else:
        its = S["items"]
        ai = st.multiselect("Author profile(s) to analyze (select several if one person is split across profiles)",
                            range(len(its)), default=[0], format_func=lambda i: its[i]["label"])
        chosen = [{"id": its[i]["id"], "name": its[i]["name"]} for i in ai]
        if any(its[i].get("works", 0) > 300 for i in ai):
            st.warning("A selected profile has 300+ works. Common names are often merged from several different "
                       "people in OpenAlex, which distorts every metric. Searching by ORCID is more reliable.")

    if chosen:
        preset = st.radio("Analysis mode", ["Quick preview", "Full analysis"], horizontal=True,
                          help="Quick preview takes the most-cited works only and finishes in about a minute.")
        lim = QUICK if preset == "Quick preview" else {"max_works": max_works, "max_citing": max_citing,
                                                       "max_refs": max_refs}
        st.caption(f"Limits: {lim['max_works']} works · {lim['max_citing']} citing papers · {lim['max_refs']} references "
                   "(the most-cited works are kept first).")
        if st.button("Start analysis", type="primary"):
            st.session_state.pop("B", None)
            bar = st.progress(0.0, text="Starting…")
            try:
                D = core.build_dataset(
                    [c["id"] for c in chosen], label=" + ".join(c["name"] for c in chosen), api_key=api_key,
                    progress=lambda msg, f: bar.progress(min(max(f, 0.0), 1.0), text=msg), **lim)
                st.session_state["D"] = D
                st.session_state["A"] = core.analyze(D)
                bar.empty()
            except Exception as e:
                bar.empty()
                st.session_state.pop("D", None)
                st.error(f"Collection failed: {e}")

# ───────────────────────── Results ─────────────────────────
D, A = st.session_state.get("D"), st.session_state.get("A")
if not D:
    footer()
    st.stop()

s = A["summary"]
is_demo = bool(D.get("demo"))
st.divider()
st.subheader(f"📚 {D['label']}")
if is_demo:
    st.info("**Synthetic demo data**: a made-up researcher generated locally to show what the app does. "
            "Nothing here describes a real person. Add an API key and search to analyze real authors.")
for w in D["warnings"]:
    st.warning(w)

tab_sum, tab_bench, tab_papers, tab_detail, tab_net, tab_int, tab_method = st.tabs(
    ["Summary", "Field benchmark", "Publications", "Paper citation map", "Author network & mutual citations",
     "Internal structure", "Method & limits"])

# ── Summary ──
with tab_sum:
    c = st.columns(5)
    c[0].metric("Works", s["works"])
    c[1].metric("Total citations", f"{s['cited_total']:,}")
    c[2].metric("h-index", s["h_index"])
    c[3].metric("i10-index", s["i10"])
    c[4].metric("Citing papers collected", f"{s['citing_works']:,}")

    st.markdown("##### Citation composition (collected citation links)")
    c = st.columns(5)
    c[0].metric("Self-citation", f"{s['self_pct']:.1f}%")
    c[1].metric("Co-author citation", f"{s['co_pct']:.1f}%",
                help="The analyzed author is not on the citing paper, but a co-author of the cited paper is.")
    c[2].metric("External citation", f"{s['ext_pct']:.1f}%")
    c[3].metric("Unique citing authors", f"{s['unique_citers']:,}", help="Excluding self; papers with 30 authors or fewer.")
    c[4].metric("Mutual-citation authors", s["mutual_n"], help="Authors who cite each other at least twice each way.")

    st.markdown("##### Citation concentration")
    c = st.columns(3)
    c[0].metric("Share involving the top citing author", f"{s['top1_share']:.1f}%")
    c[1].metric("Share involving the top 5 citing authors", f"{s['top5_share']:.1f}%")
    c[2].metric("Reference metadata coverage", f"{100 * s['refs_resolved'] / max(s['refs_total'], 1):.0f}%")

    st.markdown("##### Compared with the field")
    if st.session_state.get("B"):
        bench_cards(st.session_state["B"])
    else:
        st.info("Raw numbers mean little on their own. Open the **Field benchmark** tab to compare them with matched "
                "papers from the same field.")

    ev = A["events_df"]
    if not ev.empty:
        st.markdown("##### Citations by year of the citing paper")
        st.bar_chart(ev.pivot_table(index="citing_year", columns="kind", values="cited_id", aggfunc="count").fillna(0))

    st.markdown("##### Papers citing several of my works (excluding self-citations)")
    if A["heavy_df"].empty:
        st.caption("None.")
    else:
        show_df(A["heavy_df"], hide_index=True,
                     column_config={"Link": LINK, "Share of its references %": PCT})
    c = st.columns(3)
    with c[0]:
        csv_button("⬇ Per-paper metrics (CSV)", A["papers_df"], "papers_metrics.csv")
    with c[1]:
        csv_button("⬇ All citation links (CSV)", ev, "citation_events.csv")
    with c[2]:
        csv_button("⬇ Mutual-citation authors (CSV)", A["recip_df"], "mutual_authors.csv")

# ── Field benchmark ──
with tab_bench:
    st.markdown(
        "Is this author's citation pattern **unusual for papers like these?** Each sampled paper is compared with "
        "random papers from the **same field**: same type, publication year, OpenAlex subfield and a similar "
        "citation count (0.5×–2×).")
    with st.expander("What the three metrics mean"):
        for key, name in core.METRICS.items():
            st.markdown(f"- **{name}**: {core.METRIC_HELP[key]}")
    c = st.columns(4)
    n_target = c[0].slider("Your papers to test", 5, 40, 20, disabled=is_demo)
    k_base = c[1].slider("Baseline papers per paper", 2, 6, 3, disabled=is_demo)
    lo_hi = c[2].slider("Cited-by range of tested papers", 5, 500, (10, 300), disabled=is_demo)
    seed = c[3].number_input("Random seed", 1, 9999, 1, disabled=is_demo)
    if is_demo:
        st.caption("Demo mode: the benchmark below was generated with synthetic baselines.")
    else:
        st.caption(f"Roughly {n_target * k_base * 3} API calls (well under $0.05 of your free daily OpenAlex budget; a few "
                   "minutes on the first run, repeats are cached). Papers need at least 8 citing papers to be measured.")
        if st.button("Run benchmark", type="primary", disabled=not api_key):
            bar = st.progress(0.0, text="Starting…")
            try:
                st.session_state["B"] = core.run_benchmark(
                    D, n_target=n_target, k_base=k_base, min_cites=lo_hi[0], max_cites=lo_hi[1], seed=int(seed),
                    api_key=D.get("api_key", ""),
                    progress=lambda msg, f: bar.progress(min(max(f, 0.0), 1.0), text=msg))
            except Exception as e:
                st.error(f"Benchmark failed: {e}")
            bar.empty()
        if not api_key:
            st.warning("The benchmark needs an OpenAlex API key (sidebar).")

    B = st.session_state.get("B")
    if B:
        st.markdown(f"**{B['n_used']} of the author's papers** compared against **{B['n_baseline']} baseline papers** "
                    f"({B['n_eligible']} eligible, {B['n_sampled']} sampled).")
        if B["n_used"] == 0:
            st.warning("No paper could be compared. Widen the cited-by range, or make sure enough citing papers "
                       "were collected (use Full analysis).")
        else:
            bench_cards(B)
            st.markdown("##### Difference from matched peers, with 95% intervals")
            forest_chart(B["summary_df"])
            st.markdown("##### Paper by paper: yours vs peers")
            st.caption("Each dot is one of the author's papers. Above the dashed diagonal = higher than its matched peers.")
            cols = st.columns(len(core.METRICS))
            for col, name in zip(cols, core.METRICS.values()):
                ch = scatter_chart(B["long_df"], name)
                if ch is None:
                    col.caption(f"{name}: not enough data")
                else:
                    show_chart(ch, col)
            st.info(
                "**How to read this.** 'Above baseline' means unusual relative to comparable papers, **not misconduct**: "
                "niche fields, long-running collaborations and community citation habits all raise these numbers "
                "legitimately. Treat results from fewer than ~10 papers as anecdotal, and look at the underlying "
                "links (Paper citation map, Author network) before drawing any conclusion.")
            with st.expander("Tables"):
                show_df(B["summary_df"], hide_index=True)
                show_df(B["papers_df"], hide_index=True)
            c = st.columns(3)
            with c[0]:
                csv_button("⬇ Summary (CSV)", B["summary_df"], "benchmark_summary.csv")
            with c[1]:
                csv_button("⬇ Per paper (CSV)", B["papers_df"], "benchmark_papers.csv")
            with c[2]:
                csv_button("⬇ Chart data (CSV)", B["long_df"], "benchmark_long.csv")

# ── Publications ──
with tab_papers:
    st.caption("'Citations collected' is the number of citation links this app fetched. It can differ from "
               "OpenAlex's total when a collection limit is hit.")
    show_df(A["papers_df"].drop(columns=["id"]), hide_index=True,
                 column_config={"Link": LINK, "Self-citation %": PCT})

# ── Paper citation map ──
with tab_detail:
    pdf = A["papers_df"]
    opts = list(pdf["id"])
    labels = {r["id"]: f"{r['Year']} · {core.trunc(r['Title'], 90)} (cited by {r['Cited by (OpenAlex)']})"
              for _, r in pdf.iterrows()}
    m = st.selectbox("Select a paper", opts, format_func=lambda i: labels[i])
    paper = D["my"][m]
    st.markdown(f"**{paper['title']}** ({paper['year']}) · {core.fmt_authors(paper['authors'], 6)}")

    c = st.columns([2, 2])
    k = c[0].slider("Nodes shown per side", 5, 40, 15)
    style = c[1].radio("Node labels", ["Author, year + short title", "Author, year only (cleanest)"], horizontal=True)
    nodes, edges, om_c, om_r = core.paper_graph(D, A, m, k, "compact" if "only" in style else "short")
    st.markdown(
        "Arrows point in the direction of **'cites'**. Left = papers that cite this one; right = papers it cites. "
        "**Hover a node for the full title, authors and citation count.**  \n"
        ":red[■ self-citation] · :orange[■ co-author citation] · :blue[■ external citation] · "
        ":green[■ my other work] · ■ grey = reference · :violet[purple border = involves a mutual-citation author]")
    vis(nodes, edges, TREE, height=max(480, 62 * max(len(nodes) // 2, 6)))
    if om_c or om_r:
        st.caption(f"Not shown: {om_c} citing papers and {om_r} references (see the tables below).")

    left, right = st.columns(2)
    with left:
        st.markdown("##### Papers citing this paper")
        rows = []
        for cid, kind in A["kind_map"].get(m, {}).items():
            cw = D["citing"][cid]
            rows.append({"Year": cw["year"], "Title": cw["title"], "Authors": core.fmt_authors(cw["authors"]),
                         "Type": kind, "My papers it cites": len(cw["hits"]),
                         "Share of its references %": round(100 * len(cw["hits"]) / max(cw["n_refs"], 1), 1),
                         "Link": "https://openalex.org/" + cid})
        cdf = pd.DataFrame(rows)
        if cdf.empty:
            st.caption("No citing papers collected.")
        else:
            show_df(cdf.sort_values("Year", ascending=False), hide_index=True,
                         column_config={"Link": LINK, "Share of its references %": PCT})
    with right:
        st.markdown("##### Papers this paper cites")
        rows = []
        for r in paper["refs"]:
            v = D["refs"].get(r)
            if v:
                rows.append({"Year": v["year"], "Title": v["title"], "Authors": core.fmt_authors(v["authors"]),
                             "Cited by": v["cited_by"], "Times cited by my works": A["ref_cnt"][r],
                             "My work?": r in D["my"], "Link": "https://openalex.org/" + r})
        rdf = pd.DataFrame(rows)
        if rdf.empty:
            st.caption("No reference data.")
        else:
            show_df(rdf.sort_values("Cited by", ascending=False), hide_index=True, column_config={"Link": LINK})
        miss = len(paper["refs"]) - len(rows)
        if miss > 0:
            st.caption(f"{miss} of {len(paper['refs'])} references have no metadata and are not listed.")

# ── Author network ──
with tab_net:
    c = st.columns([3, 2, 2])
    layout_name = c[0].selectbox("Layout", list(LAYOUTS))
    k = c[1].slider("Top authors per direction", 5, 40, 15, key="k_author")
    hide_co = c[2].checkbox("Hide co-authors", value=False,
                            help="Co-authors naturally cite you a lot. Hide them to see the rest of the network.")
    layout, opts_ = LAYOUTS[layout_name]
    nodes, edges = core.author_graph(D, A, k, layout, hide_co)
    st.markdown(
        ":blue[● cites me] · :green[● I cite them] · :red[● both (mutual)] · **◆ with black border = co-author** "
        "(colour still shows direction) · numbers on edges = citation counts. "
        "Self-citations and papers with more than 30 authors are excluded. Hover for details; drag nodes to rearrange.")
    vis(nodes, edges, opts_, height=700)

    st.markdown("##### Mutual-citation authors (cite me ∩ I cite)")
    if A["recip_df"].empty:
        st.caption("None.")
    else:
        show_df(hide_id(A["recip_df"]), hide_index=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### Authors who cite me most")
        show_df(hide_id(A["inc_df"]).rename(columns={"Count": "Cites me", "Opposite direction": "I cite"}),
                     hide_index=True)
    with c2:
        st.markdown("##### Authors I cite most")
        show_df(hide_id(A["out_df"]).rename(columns={"Count": "I cite", "Opposite direction": "Cites me"}),
                     hide_index=True)

# ── Internal structure ──
with tab_int:
    st.markdown("How the author's own papers cite each other. Dot size = citations, darker = more recent, arrows = direction of citation.")
    hide = st.checkbox("Hide papers with no link to my other papers", value=True)
    nodes, edges = core.internal_graph(D, hide)
    if not nodes:
        st.caption("No internal citation links to show.")
    else:
        vis(nodes, edges, FORCE, height=680)
        st.caption(f"{len(nodes)} papers, {len(edges)} internal citations")

# ── Method ──
with tab_method:
    st.markdown("""
**Data and definitions**
- All data comes from [OpenAlex](https://openalex.org): the author's works (`author.id`), papers citing them (`cites`) and references (`referenced_works`).
- **One citation link** = one (citing paper → my paper) pair.
- **Self-citation**: the analyzed author is on the citing paper. **Co-author citation**: not the author, but a co-author of the cited paper is. **External citation**: everything else.
- **Mutual-citation author**: someone who cites me and whom I cite. Purple borders in the paper map mean at least 2 each way.
- **Quick preview** keeps only the most-cited works (up to 100) and caps citing papers and references; use **Full analysis** for complete numbers.

**Field benchmark (matched baseline)**
- For each sampled paper, the app draws random OpenAlex papers with the same type, publication year and primary subfield (falling back to field) whose citation count is within 0.5×–2×, excluding papers by the analyzed author.
- Per paper it computes: (1) % of citing papers sharing an author with the cited paper; (2) among the rest, % that include the most frequent citing author; (3) among the rest, % that include an author whose work the cited paper cites.
- Your value is compared with the mean of its own baseline; differences are averaged across papers with a bootstrap 95% CI.
- Limits: baseline papers fetch at most 300 citing papers and 200 references each; your reference lists may be partially resolved; topic matching is coarse; small samples give wide intervals; many comparisons are made at once.

**Interpretation**
- This app does **not** declare any "citation cartel". High mutual citation or concentration can be normal in narrow fields, and even a gap versus baseline is a prompt to look at the underlying links, not a finding of wrongdoing.
- Citations show influence and lineage, not causation, and carry no information about agreement or criticism.
- OpenAlex **author disambiguation errors** exist; if one person is split across profiles, select all of them. arXiv/preprint citation data can be incomplete.
- Prefer using this as a self-audit or research tool over labelling individuals.
""")
footer()
