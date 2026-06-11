"""
Two-level Louvain community detection for the legal-AI citation graph.

Implements the project-plan task: detect COMMUNITIES and then SUB-COMMUNITIES
(run Louvain again inside each community), on the citation graph.

Builds and compares TWO graph definitions, because the meeting left it open:
  A) DIRECT CITATION  — cases linked if one cites the other (the literal
     citation graph). Directed in reality; treated undirected for Louvain,
     since "A cites B" still means A and B are topically related.
  B) BIBLIOGRAPHIC COUPLING — cases linked if they cite the SAME earlier
     cases (shared precedents). Often gives tighter topical clusters.

SCOPE: works on a single bracket OR the merged corpus. Pass any citations
JSON in the same {source_doc_id, precedents:[{doc_id,...}]} shape. For the
merged graph, just point --json at the combined file — no code change.

OUTPUTS (next to input JSON, suffixed by graph type):
  - communities_<graph>.csv   doc_id, name, community, subcommunity
  - louvain_summary.txt        sizes, counts, comparison between the two graphs

USAGE:
  python louvain_communities.py
  python louvain_communities.py --json merged_all_brackets.json   # later, on full graph
  python louvain_communities.py --resolution 1.2                  # finer/coarser communities

Dependencies: networkx (Louvain is built in for networkx >= 3.0)
"""

import argparse
import csv
import json
import re
from pathlib import Path

import networkx as nx

DEFAULT_JSON = Path("citations_4501_5250.json")
DEFAULT_CSV = Path("land_property_dispute_cases.csv")
SEED = 42


def _clean_name(raw: str) -> str:
    raw = re.sub(r"_on_\d+.*$", "", raw or "")
    return re.sub(r"\s+", " ", raw.replace("_", " ")).strip()


def load_csv_names(csv_path: Path) -> dict:
    names = {}
    if not csv_path.exists():
        return names
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m = re.search(r"/doc/(\d+)/", row.get("link", "") or "")
            if m:
                names[m.group(1)] = _clean_name(row.get("case", ""))
    return names


def load_cases(path: Path, csv_names: dict) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    cases = []
    for rec in data:
        sid = rec.get("source_doc_id")
        if not sid:
            continue
        sid = str(sid)
        sname = rec.get("source_name") or ""
        if "search engine" in sname.lower() or not sname:
            sname = csv_names.get(sid, sid)
        cases.append({
            "id": sid,
            "name": csv_names.get(sid, sname),
            "cites": [str(p["doc_id"]) for p in rec.get("precedents", []) if p.get("doc_id")],
        })
    return cases


# ── graph builders ───────────────────────────────────────────────────
def build_direct_citation_graph(cases):
    """Undirected: edge between two of our cases if one cites the other."""
    ids = {c["id"] for c in cases}
    G = nx.Graph()
    G.add_nodes_from(ids)
    for c in cases:
        for tgt in c["cites"]:
            if tgt in ids:           # both ends inside the corpus
                G.add_edge(c["id"], tgt)
    return G


def build_coupling_graph(cases, min_shared=1):
    """Undirected weighted: edge weight = # precedents two cases share."""
    from collections import defaultdict
    from itertools import combinations
    prec_to_cases = defaultdict(set)
    for c in cases:
        for p in c["cites"]:
            prec_to_cases[p].add(c["id"])
    weights = defaultdict(int)
    for citing in prec_to_cases.values():
        if len(citing) < 2:
            continue
        for a, b in combinations(sorted(citing), 2):
            weights[(a, b)] += 1
    G = nx.Graph()
    G.add_nodes_from(c["id"] for c in cases)
    for (a, b), w in weights.items():
        if w >= min_shared:
            G.add_edge(a, b, weight=w)
    return G


# ── two-level Louvain ─────────────────────────────────────────────────
def two_level_louvain(G, resolution):
    """Level 1: Louvain on the whole graph. Level 2: Louvain again inside
    each community to get sub-communities. Returns {node: (comm, subcomm)}."""
    weight = "weight" if nx.get_edge_attributes(G, "weight") else None
    communities = nx.community.louvain_communities(
        G, weight=weight, resolution=resolution, seed=SEED)
    # order communities largest-first for stable numbering
    communities = sorted(communities, key=len, reverse=True)

    assignment = {}
    for ci, comm in enumerate(communities):
        sub = G.subgraph(comm)
        # only sub-cluster communities big enough to bother
        if len(comm) >= 8 and sub.number_of_edges() > 0:
            subcomms = nx.community.louvain_communities(
                sub, weight=weight, resolution=resolution, seed=SEED)
            subcomms = sorted(subcomms, key=len, reverse=True)
        else:
            subcomms = [comm]
        for si, sc in enumerate(subcomms):
            for node in sc:
                assignment[node] = (ci, si)
    # isolated nodes not in any community
    for n in G.nodes():
        assignment.setdefault(n, (-1, -1))
    return assignment, communities


def summarize(graph_label, G, assignment, communities, name):
    lines = [f"=== {graph_label} ==="]
    lines.append(f"Nodes: {G.number_of_nodes()}   Edges: {G.number_of_edges()}")
    connected = sum(1 for n in G if G.degree(n) > 0)
    lines.append(f"Connected nodes: {connected}   Isolated: {G.number_of_nodes()-connected}")
    real = [c for c in communities if len(c) > 1]
    lines.append(f"Communities (size>1): {len(real)}")
    if real:
        lines.append(f"Sizes (top 8): {[len(c) for c in real[:8]]}")
    try:
        mod = nx.community.modularity(
            G, communities, weight=("weight" if nx.get_edge_attributes(G, "weight") else None))
        lines.append(f"Modularity: {mod:.4f}   (higher = cleaner community structure; >0.3 is meaningful)")
    except Exception:
        pass
    # show a sample from the largest communities
    lines.append("Largest communities (sample case names):")
    for ci, comm in enumerate(real[:5]):
        sample = "; ".join(name.get(x, x)[:32] for x in list(comm)[:3])
        # count sub-communities in this community
        subs = {assignment[x][1] for x in comm if x in assignment}
        lines.append(f"  Community {ci}: {len(comm)} cases, {len(subs)} sub-communities — e.g. {sample}")
    return "\n".join(lines)


def write_assignments(path, G, assignment, name):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["doc_id", "name", "community", "subcommunity", "degree"])
        for n in sorted(G.nodes(), key=lambda x: assignment.get(x, (-1, -1))):
            comm, sub = assignment.get(n, (-1, -1))
            w.writerow([n, name.get(n, n), comm, sub, G.degree(n)])


def main():
    ap = argparse.ArgumentParser(description="Two-level Louvain on citation + coupling graphs.")
    ap.add_argument("--json", type=Path, default=DEFAULT_JSON)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--resolution", type=float, default=1.0,
                    help="Louvain resolution: >1 = more, smaller communities (default 1.0)")
    ap.add_argument("--min-shared", type=int, default=1,
                    help="min shared precedents for a coupling edge (default 1)")
    args = ap.parse_args()

    if not args.json.exists():
        print(f"ERROR: file not found: {args.json}")
        return

    csv_names = load_csv_names(args.csv)
    cases = load_cases(args.json, csv_names)
    name = {c["id"]: c["name"] for c in cases}
    print(f"Loaded {len(cases)} cases")
    out = args.json.parent
    report = []

    # Graph A: direct citation
    GA = build_direct_citation_graph(cases)
    asgnA, commsA = two_level_louvain(GA, args.resolution)
    write_assignments(out / "communities_direct.csv", GA, asgnA, name)
    sumA = summarize("DIRECT CITATION GRAPH", GA, asgnA, commsA, name)
    report.append(sumA)
    print("\n" + sumA)

    # Graph B: bibliographic coupling
    GB = build_coupling_graph(cases, args.min_shared)
    asgnB, commsB = two_level_louvain(GB, args.resolution)
    write_assignments(out / "communities_coupling.csv", GB, asgnB, name)
    sumB = summarize("BIBLIOGRAPHIC COUPLING GRAPH", GB, asgnB, commsB, name)
    report.append(sumB)
    print("\n" + sumB)

    # comparison note
    realA = len([c for c in commsA if len(c) > 1])
    realB = len([c for c in commsB if len(c) > 1])
    cmp = ["\n=== COMPARISON ===",
           f"Direct citation : {realA} communities, {GA.number_of_edges()} edges",
           f"Coupling        : {realB} communities, {GB.number_of_edges()} edges",
           "Coupling usually yields tighter topical clusters (cases sharing many",
           "precedents are about the same issue); direct citation reflects actual",
           "reference structure. Compare modularity above — higher = cleaner split.",
           "",
           "SCOPE NOTE: run on this bracket as a prototype. Re-run with --json",
           "pointed at the merged corpus JSON for the team's final communities;",
           "no code change needed."]
    report.append("\n".join(cmp))
    print("\n".join(cmp))

    (out / "louvain_summary.txt").write_text("\n\n".join(report), encoding="utf-8")
    print(f"\nWrote: communities_direct.csv, communities_coupling.csv, louvain_summary.txt")


if __name__ == "__main__":
    main()
