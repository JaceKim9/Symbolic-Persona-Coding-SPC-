"""Citation Trace: trace one researcher's citation network with OpenAlex data."""
import json

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import core

st.set_page_config(page_title="Citation Trace", page_icon="🕸️", layout="wide")

LINK = st.column_config.LinkColumn("Link", display_text="Open")
PCT = st.column_config.NumberColumn(format="%.1f%%")


# ───────────────────────── Helpers ─────────────────────────
def vis(nodes, edges, options, height=640):
    """Render a vis-network graph inside an iframe."""
    def dump(o):
        return json.dumps(o, ensure_ascii=False).replace("</", "<\\/")

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>html,body{{margin:0}}#g{{width:100%;height:{height}px;border:1px solid #ddd;border-radius:8px;background:#fff}}</style>
</head><body><div id="g"></div><script>
const nodes = new vis.DataSet({dump(nodes)});
const edges = new vis.DataSet({dump(edges)});
new vis.Network(document.getElementById('g'), {{nodes, edges}}, {dump(options)});
</script></body></html>"""
    components.html(html, height=height + 10, scrolling=False)


PHYSICS = {
    "physics": {"solver": "forceAtlas2Based", "stabilization": {"iterations": 200}},
    "edges": {"arrows": "to", "smooth": {"type": "curvedCW", "roundness": 0.15}},
    "interaction": {"hover": True, "tooltipDelay": 80},
}
TREE = {
    "layout": {"hierarchical": {"enabled": True, "direction": "LR", "sortMethod": "directed",
                                "levelSeparation": 330, "nodeSpacing": 55}},
    "physics": {"enabled": False},
    "edges": {"arrows": "to", "smooth": {"type": "cubicBezier"}, "color": {"color": "#9ca3af"}},
    "interaction": {"hover": True, "tooltipDelay": 80, "dragNodes": False},
}


def csv_button(label, df, name):
    st.download_button(label, df.to_csv(index=False).encode("utf-8-sig"), file_name=name, mime="text/csv")


def hide_id(df):
    return df.drop(columns=["id"], errors="ignore")


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
        else:
            st.warning("No API key: OpenAlex only allows a tiny shared free budget without one, so requests will "
                       "most likely fail. Get a free key at openalex.org/settings/api.")
    max_works = st.slider("Max works", 50, 1000, 500, 50)
    max_citing = st.slider("Max citing papers to collect", 500, 10000, 4000, 500)
    max_refs = st.slider("Max reference records to collect", 500, 6000, 3000, 500)
    st.caption("Larger values are more complete but slower on the first run. Identical requests are cached.")

# ───────────────────────── Header / search ─────────────────────────
st.title("🕸️ Citation Trace")
st.caption("Start from an ORCID, an author name, or a single paper. See the author's full publication list and, "
           "for each paper, who cites it, what it cites, and how that compares with the field. Data: OpenAlex.")

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

    if chosen and st.button("Start analysis", type="primary"):
        st.session_state.pop("B", None)
        bar = st.progress(0.0, text="Starting…")
        try:
            D = core.build_dataset(
                [c["id"] for c in chosen], label=" + ".join(c["name"] for c in chosen), api_key=api_key,
                max_works=max_works, max_citing=max_citing, max_refs=max_refs,
                progress=lambda msg, f: bar.progress(min(max(f, 0.0), 1.0), text=msg))
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
    st.stop()

s = A["summary"]
st.divider()
st.subheader(f"📚 {D['label']}")
for w in D["warnings"]:
    st.warning(w)

tab_sum, tab_papers, tab_detail, tab_net, tab_bench, tab_int, tab_method = st.tabs(
    ["Summary", "Publications", "Paper citation map", "Author network & mutual citations",
     "Field benchmark", "Internal structure", "Method & limits"])

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
    c[3].metric("Unique citing authors", f"{s['unique_citers']:,}",
                help="Excluding self; papers with 30 authors or fewer.")
    c[4].metric("Mutual-citation authors", s["mutual_n"], help="Authors who cite each other at least twice each way.")

    st.markdown("##### Citation concentration")
    c = st.columns(3)
    c[0].metric("Share involving the top citing author", f"{s['top1_share']:.1f}%")
    c[1].metric("Share involving the top 5 citing authors", f"{s['top5_share']:.1f}%")
    c[2].metric("Reference metadata coverage", f"{100 * s['refs_resolved'] / max(s['refs_total'], 1):.0f}%")
    st.info("These are **reference indicators, not verdicts.** Raw numbers mean little on their own: use the "
            "**Field benchmark** tab to compare them with matched papers from the same field.")

    ev = A["events_df"]
    if not ev.empty:
        st.markdown("##### Citations by year of the citing paper")
        st.bar_chart(ev.pivot_table(index="citing_year", columns="kind", values="cited_id",
                                    aggfunc="count").fillna(0))

    st.markdown("##### Papers citing several of my works (excluding self-citations)")
    if A["heavy_df"].empty:
        st.caption("None.")
    else:
        st.dataframe(A["heavy_df"], hide_index=True, use_container_width=True,
                     column_config={"Link": LINK, "Share of its references %": PCT})
    c = st.columns(3)
    with c[0]:
        csv_button("⬇ Per-paper metrics (CSV)", A["papers_df"], "papers_metrics.csv")
    with c[1]:
        csv_button("⬇ All citation links (CSV)", ev, "citation_events.csv")
    with c[2]:
        csv_button("⬇ Mutual-citation authors (CSV)", A["recip_df"], "mutual_authors.csv")

# ── Publications ──
with tab_papers:
    st.caption("'Citations collected' is the number of citation links this app fetched. It can differ from "
               "OpenAlex's total when a collection limit is hit.")
    st.dataframe(A["papers_df"].drop(columns=["id"]), hide_index=True, use_container_width=True,
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

    k = st.slider("Nodes shown per side", 5, 60, 20)
    nodes, edges, om_c, om_r = core.paper_graph(D, A, m, k)
    st.markdown(
        "Arrows point in the direction of **'cites'**. Left = papers that cite this one; right = papers it cites.  \n"
        ":red[■ self-citation] · :orange[■ co-author citation] · :blue[■ external citation] · "
        ":green[■ my other work] · ■ grey = reference · :violet[purple border = involves a mutual-citation author]")
    vis(nodes, edges, TREE, height=max(500, 34 * max(len(nodes) // 2, 8)))
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
            st.dataframe(cdf.sort_values("Year", ascending=False), hide_index=True, use_container_width=True,
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
            st.dataframe(rdf.sort_values("Cited by", ascending=False), hide_index=True,
                         use_container_width=True, column_config={"Link": LINK})
        miss = len(paper["refs"]) - len(rows)
        if miss > 0:
            st.caption(f"{miss} of {len(paper['refs'])} references have no metadata and are not listed.")

# ── Author network ──
with tab_net:
    st.markdown(
        "Author-level view: **who cites me (blue), whom I cite (green), and who does both (red).** "
        "Numbers on edges are citation counts; ◆ marks co-authors. Self-citations and papers with more than "
        "30 authors are excluded.")
    k = st.slider("Top authors per direction", 5, 40, 15, key="k_author")
    nodes, edges = core.author_graph(D, A, k)
    vis(nodes, edges, PHYSICS, height=680)

    st.markdown("##### Mutual-citation authors (cite me ∩ I cite)")
    if A["recip_df"].empty:
        st.caption("None.")
    else:
        st.dataframe(hide_id(A["recip_df"]), hide_index=True, use_container_width=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### Authors who cite me most")
        st.dataframe(hide_id(A["inc_df"]).rename(columns={"Count": "Cites me", "Opposite direction": "I cite"}),
                     hide_index=True, use_container_width=True)
    with c2:
        st.markdown("##### Authors I cite most")
        st.dataframe(hide_id(A["out_df"]).rename(columns={"Count": "I cite", "Opposite direction": "Cites me"}),
                     hide_index=True, use_container_width=True)

# ── Benchmark ──
with tab_bench:
    st.markdown(
        "Compares your papers with **randomly drawn papers from the same field**: same type, publication year, "
        "OpenAlex subfield, and a similar citation count (0.5×–2×). Each of your papers is measured on three "
        "metrics and compared with its own matched baseline, so the result answers *'is this unusual for papers like these?'*")
    with st.expander("What the three metrics mean"):
        for key, name in core.METRICS.items():
            st.markdown(f"- **{name}**: {core.METRIC_HELP[key]}")
    c = st.columns(4)
    n_target = c[0].slider("Your papers to test", 5, 40, 20)
    k_base = c[1].slider("Baseline papers per paper", 2, 6, 3)
    lo_hi = c[2].slider("Cited-by range of tested papers", 5, 500, (10, 300))
    seed = c[3].number_input("Random seed", 1, 9999, 1)
    st.caption(f"Roughly {n_target * k_base * 3} API calls (well under $0.05 of your free daily OpenAlex budget; "
               "a few minutes on the first run, repeats are cached). Papers need at least 8 citing papers to be measured.")

    if st.button("Run benchmark", type="primary"):
        bar = st.progress(0.0, text="Starting…")
        try:
            st.session_state["B"] = core.run_benchmark(
                D, n_target=n_target, k_base=k_base, min_cites=lo_hi[0], max_cites=lo_hi[1], seed=int(seed),
                api_key=D.get("api_key", ""), progress=lambda msg, f: bar.progress(min(max(f, 0.0), 1.0), text=msg))
        except Exception as e:
            st.error(f"Benchmark failed: {e}")
        bar.empty()

    B = st.session_state.get("B")
    if B:
        st.markdown(f"**{B['n_used']} of your papers** compared against **{B['n_baseline']} baseline papers** "
                    f"({B['n_eligible']} eligible, {B['n_sampled']} sampled).")
        if B["n_used"] == 0:
            st.warning("No paper could be compared. Widen the cited-by range, or make sure enough citing papers "
                       "were collected (sidebar limits).")
        else:
            sdf = B["summary_df"]
            st.dataframe(sdf, hide_index=True, use_container_width=True)
            chart = sdf.dropna(subset=["Your mean"]).set_index("Metric")[["Your mean", "Baseline mean"]]
            if not chart.empty:
                st.bar_chart(chart)
            st.info(
                "**How to read this.** *Mean difference* is yours minus the matched baseline, in percentage points, "
                "with a bootstrap 95% confidence interval. If the interval includes 0, your papers are not "
                "distinguishable from comparable papers. 'Above baseline' means unusual relative to peers, not "
                "misconduct: niche fields, long-running collaborations, and community-specific citation habits "
                "all raise these numbers legitimately. Treat results from fewer than ~10 papers as anecdotal.")
            st.markdown("##### Paper-by-paper")
            st.dataframe(B["papers_df"], hide_index=True, use_container_width=True)
            csv_button("⬇ Benchmark per paper (CSV)", B["papers_df"], "benchmark_papers.csv")
            csv_button("⬇ Benchmark summary (CSV)", sdf, "benchmark_summary.csv")

# ── Internal structure ──
with tab_int:
    st.markdown("How the author's own papers cite each other. Dot size = citations, darker = more recent, arrows = direction of citation.")
    hide = st.checkbox("Hide papers with no link to my other papers", value=True)
    nodes, edges = core.internal_graph(D, hide)
    if not nodes:
        st.caption("No internal citation links to show.")
    else:
        vis(nodes, edges, PHYSICS, height=680)
        st.caption(f"{len(nodes)} papers, {len(edges)} internal citations")

# ── Method ──
with tab_method:
    st.markdown("""
**Data and definitions**
- All data comes from [OpenAlex](https://openalex.org): the author's works (`author.id`), papers citing them (`cites`) and references (`referenced_works`).
- **One citation link** = one (citing paper → my paper) pair.
- **Self-citation**: the analyzed author is on the citing paper. **Co-author citation**: not the author, but a co-author of the cited paper is. **External citation**: everything else.
- **Mutual-citation author**: someone who cites me and whom I cite. Purple borders in the graph mean at least 2 each way.

**Field benchmark (matched baseline)**
- For each sampled paper of yours, the app draws random OpenAlex papers with the same type, publication year and primary subfield (falling back to field) whose citation count is within 0.5×–2×, excluding papers by the analyzed author.
- Per paper it computes: (1) % of citing papers sharing an author with the cited paper; (2) among the rest, % that include the most frequent citing author; (3) among the rest, % that include an author whose work the cited paper cites.
- Your value is compared with the mean of its own baseline; differences are averaged across papers with a bootstrap 95% CI.
- Limits: baseline papers fetch at most 300 citing papers and 200 references each; your reference lists may be partially resolved (sidebar limits); matching on topic is coarse; small samples give wide intervals; many comparisons are made at once.

**Interpretation**
- This app does **not** declare any "citation cartel". High mutual citation or concentration can be normal in narrow fields, and even a gap versus baseline is a prompt to look at the underlying links, not a finding of wrongdoing.
- Citations show influence and lineage, not causation, and carry no information about agreement or criticism.
- OpenAlex **author disambiguation errors** exist; if one person is split across profiles, select all of them. arXiv/preprint citation data can be incomplete.
- Prefer using this as a self-audit or research tool over labelling individuals.
""")
