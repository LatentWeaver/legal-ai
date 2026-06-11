"""
Citation relationship analysis for the scraped Indian Kanoon bracket.

Reads citations_4501_5250.json and computes two kinds of "relatedness":

  1. BIBLIOGRAPHIC COUPLING
     Two of YOUR cases are coupled if they cite the same earlier case(s).
     Shared precedents -> likely same legal issue. (forward similarity)

  2. CO-CITATION
     Two earlier cases are co-cited if one of YOUR cases cites BOTH of them.
     Frequently co-cited -> foundational pair for a doctrine. (backward similarity)

OUTPUTS (written next to the input JSON):
  - coupling_pairs.csv        ranked case pairs by # shared precedents
  - cocitation_pairs.csv      ranked precedent pairs by # times co-cited
  - coupling_edges.csv        edge list (source,target,weight) for Louvain/Gephi
  - cocitation_edges.csv      edge list for Louvain/Gephi
  - clustering_summary.txt     overall "how clustered is my bracket" report
  - citation_network.html      interactive network graph (open in a browser)

USAGE:
  python analyze_citations.py
  python analyze_citations.py --json citations_4501_5250.json --min-shared 2

Dependencies: networkx  (pip install networkx)
The HTML graph is self-contained (vis-network via CDN) — no extra installs.
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import networkx as nx

DEFAULT_JSON = Path("citations_4501_5250.json")
DEFAULT_CSV = Path("land_property_dispute_cases.csv")


def _clean_name(raw: str) -> str:
    """'Abdul_Rahman_vs_..._on_20_November_2001' -> 'Abdul Rahman vs ...'."""
    raw = re.sub(r"_on_\d+.*$", "", raw or "")
    return re.sub(r"\s+", " ", raw.replace("_", " ")).strip()


def load_csv_names(csv_path: Path) -> dict:
    """Map doc_id -> readable case name from the source CSV (reliable names)."""
    names = {}
    if not csv_path.exists():
        return names
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m = re.search(r"/doc/(\d+)/", row.get("link", "") or "")
            if m:
                names[m.group(1)] = _clean_name(row.get("case", ""))
    return names


# ── load ───────────────────────────────────────────────────────────────
def load_cases(path: Path, csv_names: dict) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    cases = []
    for rec in data:
        sid = rec.get("source_doc_id")
        if not sid:
            continue
        sid = str(sid)
        # Prefer the CSV name (reliable). Fall back to source_name only if it's
        # not the generic "Indian Kanoon - Search engine" placeholder.
        sname = rec.get("source_name") or ""
        if "search engine" in sname.lower() or not sname:
            sname = csv_names.get(sid, sid)
        cases.append({
            "id": sid,
            "name": csv_names.get(sid, sname),
            "cites": [str(p["doc_id"]) for p in rec.get("precedents", []) if p.get("doc_id")],
            "cited_by": [str(c["doc_id"]) for c in rec.get("cited_by", []) if c.get("doc_id")],
            "cite_names": {str(p["doc_id"]): p.get("name", "") for p in rec.get("precedents", []) if p.get("doc_id")},
        })
    return cases


# ── 1. bibliographic coupling (shared precedents) ───────────────────────
def bibliographic_coupling(cases, min_shared):
    """Pairs of YOUR cases that cite the same earlier cases."""
    # invert: precedent_id -> set of our cases that cite it
    prec_to_cases = defaultdict(set)
    for c in cases:
        for p in c["cites"]:
            prec_to_cases[p].add(c["id"])

    # count shared precedents per case-pair
    pair_shared = defaultdict(set)  # (a,b) -> set of shared precedent ids
    for prec, citing in prec_to_cases.items():
        if len(citing) < 2:
            continue
        for a, b in combinations(sorted(citing), 2):
            pair_shared[(a, b)].add(prec)

    name = {c["id"]: c["name"] for c in cases}
    rows = []
    for (a, b), shared in pair_shared.items():
        if len(shared) >= min_shared:
            rows.append({
                "case_a_id": a, "case_a_name": name.get(a, a),
                "case_b_id": b, "case_b_name": name.get(b, b),
                "shared_precedents": len(shared),
                "shared_ids": ";".join(sorted(shared)),
            })
    rows.sort(key=lambda r: r["shared_precedents"], reverse=True)
    return rows


# ── 2. co-citation (precedents cited together) ──────────────────────────
def co_citation(cases, min_together):
    """Pairs of EARLIER cases that are cited together by your cases."""
    pair_count = defaultdict(int)       # (p,q) -> times co-cited
    pair_by = defaultdict(set)          # (p,q) -> which of our cases co-cite them
    prec_name = {}
    for c in cases:
        for pid, pname in c["cite_names"].items():
            prec_name.setdefault(pid, pname)
        for p, q in combinations(sorted(set(c["cites"])), 2):
            pair_count[(p, q)] += 1
            pair_by[(p, q)].add(c["id"])

    rows = []
    for (p, q), n in pair_count.items():
        if n >= min_together:
            rows.append({
                "prec_p_id": p, "prec_p_name": prec_name.get(p, p),
                "prec_q_id": q, "prec_q_name": prec_name.get(q, q),
                "times_co_cited": n,
                "co_cited_by_cases": ";".join(sorted(pair_by[(p, q)])),
            })
    rows.sort(key=lambda r: r["times_co_cited"], reverse=True)
    return rows


# ── output writers ──────────────────────────────────────────────────────
def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_coupling_edges(path, coupling_rows):
    """source,target,weight for Louvain/Gephi — weight = shared precedents."""
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source", "target", "weight"])
        for r in coupling_rows:
            w.writerow([r["case_a_id"], r["case_b_id"], r["shared_precedents"]])


def write_cocitation_edges(path, cocit_rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source", "target", "weight"])
        for r in cocit_rows:
            w.writerow([r["prec_p_id"], r["prec_q_id"], r["times_co_cited"]])


# ── clustering summary ──────────────────────────────────────────────────
def clustering_summary(cases, coupling_rows):
    """Build the coupling graph and report how clustered the bracket is."""
    G = nx.Graph()
    G.add_nodes_from(c["id"] for c in cases)
    for r in coupling_rows:
        G.add_edge(r["case_a_id"], r["case_b_id"], weight=r["shared_precedents"])

    lines = []
    lines.append("=== BIBLIOGRAPHIC COUPLING — CLUSTERING SUMMARY ===\n")
    lines.append(f"Cases in bracket:            {len(cases)}")
    lines.append(f"Cases with >=1 coupling:     {sum(1 for n in G if G.degree(n) > 0)}")
    lines.append(f"Coupling edges (pairs):      {G.number_of_edges()}")

    components = [c for c in nx.connected_components(G) if len(c) > 1]
    components.sort(key=len, reverse=True)
    lines.append(f"Connected clusters (size>1): {len(components)}")
    if components:
        lines.append(f"Largest cluster size:        {len(components[0])}")

    # community detection (Louvain is built into networkx >=3.0)
    try:
        comms = nx.community.louvain_communities(G, weight="weight", seed=42)
        comms = [c for c in comms if len(c) > 1]
        comms.sort(key=len, reverse=True)
        lines.append(f"Louvain communities (size>1):{len(comms)}")
        name = {c["id"]: c["name"] for c in cases}
        lines.append("\nTop communities (by size):")
        for i, comm in enumerate(comms[:5], 1):
            sample = list(comm)[:3]
            sample_names = "; ".join(name.get(s, s)[:40] for s in sample)
            lines.append(f"  Community {i}: {len(comm)} cases  e.g. {sample_names}")
    except Exception as e:
        lines.append(f"(Louvain unavailable: {e})")

    # most-connected cases
    deg = sorted(G.degree(weight="weight"), key=lambda x: x[1], reverse=True)
    name = {c["id"]: c["name"] for c in cases}
    lines.append("\nMost-connected cases (by weighted coupling degree):")
    for nid, d in deg[:10]:
        if d == 0:
            break
        lines.append(f"  {name.get(nid, nid)[:55]:<57} (degree {d})")

    return "\n".join(lines), G


# ── interactive HTML graph ──────────────────────────────────────────────
def write_html_graph(path, cases, coupling_rows, G):
    """Readable interactive network. Only hub nodes are labeled by default;
    a slider filters weak edges live so the structure is legible."""
    name = {c["id"]: c["name"] for c in cases}
    try:
        comms = nx.community.louvain_communities(G, weight="weight", seed=42)
    except Exception:
        comms = []
    node_comm = {}
    for i, comm in enumerate(comms):
        for n in comm:
            node_comm[n] = i

    active = set()
    for r in coupling_rows:
        active.add(r["case_a_id"]); active.add(r["case_b_id"])

    # weighted degree -> who's a "hub". Only hubs get a permanent label.
    wdeg = dict(G.degree(weight="weight"))
    if active:
        sorted_deg = sorted((wdeg.get(n, 0) for n in active), reverse=True)
        # label roughly the top 15% most-connected nodes
        cutoff_idx = max(0, int(len(sorted_deg) * 0.15) - 1)
        label_cutoff = sorted_deg[cutoff_idx] if sorted_deg else 0
    else:
        label_cutoff = 0

    nodes = [{
        "id": nid,
        "label": (name.get(nid, nid)[:28] if wdeg.get(nid, 0) >= label_cutoff and label_cutoff > 0 else ""),
        "title": f"{name.get(nid, nid)}  (shared-precedent degree: {wdeg.get(nid,0)})",
        "group": node_comm.get(nid, 0),
        "value": max(1, wdeg.get(nid, 0)),
    } for nid in active]

    edges = [{
        "from": r["case_a_id"], "to": r["case_b_id"],
        "value": r["shared_precedents"],
        "weight": r["shared_precedents"],
        "title": f"{r['shared_precedents']} shared precedents",
    } for r in coupling_rows]

    max_w = max((r["shared_precedents"] for r in coupling_rows), default=1)

    html = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Citation Network — Bibliographic Coupling</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background: #0d1117; color: #e6edf3; }
  #header { padding: 12px 16px; border-bottom: 1px solid #30363d; }
  #header h2 { margin: 0 0 4px; }
  #header p { margin: 0 0 8px; color: #8b949e; font-size: 13px; max-width: 900px; }
  #controls { display: flex; gap: 16px; align-items: center; font-size: 13px; color: #c9d1d9; }
  #controls input { vertical-align: middle; }
  #stat { color: #58a6ff; }
  #net { width: 100%; height: calc(100vh - 130px); }
</style></head>
<body>
<div id="header">
  <h2>Citation Network — Bibliographic Coupling</h2>
  <p>Each dot is a case in your bracket. Two cases are linked when they cite the same earlier case(s) — a proxy for "about the same legal issue." Bigger dot = more connections. Colors = auto-detected communities (likely topic clusters). Only the most-connected cases are labeled; <b>hover any dot</b> for its name, <b>drag</b> to explore.</p>
  <div id="controls">
    <label>Min shared precedents to show a link:
      <input type="range" id="thresh" min="1" max="__MAXW__" value="1">
      <span id="threshval">1</span>
    </label>
    <span id="stat"></span>
  </div>
</div>
<div id="net"></div>
<script>
  const allEdges = __EDGES__;
  const nodes = new vis.DataSet(__NODES__);
  const edges = new vis.DataSet(allEdges);
  const container = document.getElementById('net');
  const network = new vis.Network(container, { nodes, edges }, {
    nodes: { shape: 'dot', scaling: { min: 6, max: 45 }, font: { color: '#e6edf3', size: 13, strokeWidth: 3, strokeColor: '#0d1117' } },
    edges: { color: { color: '#30363d', highlight: '#58a6ff' }, scaling: { min: 1, max: 8 }, smooth: false },
    physics: { stabilization: { iterations: 250 }, barnesHut: { gravitationalConstant: -12000, springLength: 150, avoidOverlap: 0.3 } },
    interaction: { hover: true, tooltipDelay: 80 }
  });

  const thresh = document.getElementById('thresh');
  const threshval = document.getElementById('threshval');
  const stat = document.getElementById('stat');
  function applyFilter() {
    const t = +thresh.value; threshval.textContent = t;
    const keep = allEdges.filter(e => e.weight >= t);
    edges.clear(); edges.add(keep);
    stat.textContent = keep.length + ' links shown';
  }
  thresh.addEventListener('input', applyFilter);
  applyFilter();
</script>
</body></html>"""
    html = (html
            .replace("__MAXW__", str(max_w))
            .replace("__NODES__", json.dumps(nodes))
            .replace("__EDGES__", json.dumps(edges)))
    path.write_text(html, encoding="utf-8")


# ── main ─────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Analyze citation relationships in the scraped bracket.")
    ap.add_argument("--json", type=Path, default=DEFAULT_JSON)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                    help="source CSV for reliable case names (default: land_property_dispute_cases.csv)")
    ap.add_argument("--min-shared", type=int, default=1,
                    help="min shared precedents to count as a coupling pair (default 1)")
    ap.add_argument("--min-together", type=int, default=2,
                    help="min times two precedents must be co-cited to report (default 2)")
    args = ap.parse_args()

    if not args.json.exists():
        print(f"ERROR: file not found: {args.json}")
        return

    csv_names = load_csv_names(args.csv)
    if csv_names:
        print(f"Loaded {len(csv_names)} case names from {args.csv.name}")
    else:
        print(f"(no CSV names found at {args.csv} — nodes will use doc IDs)")
    cases = load_cases(args.json, csv_names)
    print(f"Loaded {len(cases)} cases from {args.json.name}")
    out = args.json.parent

    # 1. coupling
    coupling = bibliographic_coupling(cases, args.min_shared)
    write_csv(out / "coupling_pairs.csv", coupling,
              ["case_a_id", "case_a_name", "case_b_id", "case_b_name", "shared_precedents", "shared_ids"])
    write_coupling_edges(out / "coupling_edges.csv", coupling)
    print(f"  Bibliographic coupling: {len(coupling)} related case-pairs "
          f"(>= {args.min_shared} shared precedent)")

    # 2. co-citation
    cocit = co_citation(cases, args.min_together)
    write_csv(out / "cocitation_pairs.csv", cocit,
              ["prec_p_id", "prec_p_name", "prec_q_id", "prec_q_name", "times_co_cited", "co_cited_by_cases"])
    write_cocitation_edges(out / "cocitation_edges.csv", cocit)
    print(f"  Co-citation: {len(cocit)} precedent-pairs co-cited "
          f">= {args.min_together} times")

    # 3. summary + 4. graph
    summary, G = clustering_summary(cases, coupling)
    (out / "clustering_summary.txt").write_text(summary, encoding="utf-8")
    write_html_graph(out / "citation_network.html", cases, coupling, G)

    print("\nWrote:")
    for fn in ["coupling_pairs.csv", "coupling_edges.csv", "cocitation_pairs.csv",
               "cocitation_edges.csv", "clustering_summary.txt", "citation_network.html"]:
        print(f"  {fn}")
    print("\n--- quick summary ---")
    print(summary[:600])


if __name__ == "__main__":
    main()
