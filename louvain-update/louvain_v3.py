""" Dependencies: networkx >= 3.0 """

import argparse
import csv
import json
import re
import statistics
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


def build_directed_graph(cases):
    ids = {c["id"] for c in cases}
    DG = nx.DiGraph()
    DG.add_nodes_from(ids)
    for c in cases:
        for tgt in c["cites"]:
            if tgt in ids:
                DG.add_edge(c["id"], tgt)
    return DG


# ── top-k significant selection ────────────────────────────────────────
def select_significant(communities, mode, top_k):
    real = [(i, c) for i, c in enumerate(communities) if len(c) > 1]
    sizes = [len(c) for _, c in real]
    if not sizes:
        return [], "No communities of size > 1."
    if mode == "topk":
        chosen = [i for i, _ in real[:top_k]]
        return chosen, f"Selected the {len(chosen)} largest communities (--top-k {top_k})."
    mean = statistics.mean(sizes)
    std = statistics.pstdev(sizes) if len(sizes) > 1 else 0
    cutoff = mean + std
    chosen = [i for i, c in real if len(c) > cutoff]
    if not chosen and real:
        chosen = [real[0][0]]
    rationale = (f"Auto: communities larger than mean+1std.\n"
                 f"  sizes(>1): {sizes}\n  mean={mean:.1f} std={std:.1f} cutoff>{cutoff:.1f}\n"
                 f"  -> {len(chosen)} significantly-larger communities sub-clustered.")
    return chosen, rationale


def two_level(G, mode, top_k):
    """G may be directed (uses directed modularity) or undirected."""
    communities = nx.community.louvain_communities(G, seed=SEED)
    communities = sorted(communities, key=len, reverse=True)
    selected, rationale = select_significant(communities, mode, top_k)
    sel = set(selected)
    assignment = {}
    for ci, comm in enumerate(communities):
        if ci in sel and len(comm) >= 4:
            sub = G.subgraph(comm)
            subs = sorted(nx.community.louvain_communities(sub, seed=SEED), key=len, reverse=True)
        else:
            subs = [comm]
        for si, sc in enumerate(subs):
            for n in sc:
                assignment[n] = (ci, si)
    for n in G.nodes():
        assignment.setdefault(n, (-1, -1))
    return communities, selected, rationale, assignment


def modularity_of(G, communities):
    try:
        return nx.community.modularity(G, communities)
    except Exception:
        return float("nan")


def write_assignments(path, DG, assignment, name):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["doc_id", "name", "community", "subcommunity", "out_citations", "in_citations"])
        for n in sorted(DG.nodes(), key=lambda x: assignment.get(x, (-1, -1))):
            comm, sub = assignment.get(n, (-1, -1))
            w.writerow([n, name.get(n, n), comm, sub, DG.out_degree(n), DG.in_degree(n)])


def summarize(label, G, communities, selected, rationale, assignment, name):
    L = [f"=== {label} ==="]
    L.append(f"Nodes: {G.number_of_nodes()}  Edges: {G.number_of_edges()}")
    real = [c for c in communities if len(c) > 1]
    L.append(f"Communities (size>1): {len(real)}   Modularity: {modularity_of(G, communities):.4f}")
    L.append(rationale)
    L.append("Selected (sub-clustered) communities:")
    for ci in selected:
        comm = communities[ci]
        subs = {assignment[n][1] for n in comm}
        sample = "; ".join(name.get(x, x)[:28] for x in list(comm)[:3])
        L.append(f"  C{ci}: {len(comm)} cases -> {len(subs)} subs | e.g. {sample}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Louvain v3: directed + undirected, top-k sub-communities.")
    ap.add_argument("--json", type=Path, default=DEFAULT_JSON)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--select", choices=["auto", "topk"], default="auto")
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()

    if not args.json.exists():
        print(f"ERROR: file not found: {args.json}")
        return

    csv_names = load_csv_names(args.csv)
    cases = load_cases(args.json, csv_names)
    name = {c["id"]: c["name"] for c in cases}
    print(f"Loaded {len(cases)} cases")

    DG = build_directed_graph(cases)
    UG = DG.to_undirected()

    # directed run (directed modularity)
    d_comms, d_sel, d_rat, d_asg = two_level(DG, args.select, args.top_k)
    write_assignments(args.json.parent / "communities_directed.csv", DG, d_asg, name)
    d_sum = summarize("DIRECTED CITATION GRAPH (directed modularity)", DG, d_comms, d_sel, d_rat, d_asg, name)

    # undirected run (standard modularity)
    u_comms, u_sel, u_rat, u_asg = two_level(UG, args.select, args.top_k)
    write_assignments(args.json.parent / "communities_undirected.csv", DG, u_asg, name)
    u_sum = summarize("UNDIRECTED CITATION GRAPH (standard modularity)", UG, u_comms, u_sel, u_rat, u_asg, name)

    d_real = len([c for c in d_comms if len(c) > 1])
    u_real = len([c for c in u_comms if len(c) > 1])
    cmp = [
        "=== DIRECTED vs UNDIRECTED COMPARISON ===",
        f"Directed   : {d_real} communities, modularity {modularity_of(DG, d_comms):.4f}",
        f"Undirected : {u_real} communities, modularity {modularity_of(UG, u_comms):.4f}",
        "",
        "NetworkX applies the directed modularity gain (Dugue & Perez) on a DiGraph,",
        "so the two partitions differ. Directed respects citation direction (who cites",
        "whom); undirected treats a citation as a symmetric link. Review both with the",
        "team to decide which better fits the precedent-finding goal.",
        "",
        "Method follows the lab's NARRA-SCALE two-level pattern (communities then",
        "sub-communities). Prototype on this bracket; re-run on the merged corpus.",
    ]
    report = "\n\n".join([d_sum, u_sum, "\n".join(cmp)])
    (args.json.parent / "louvain_v3_summary.txt").write_text(report, encoding="utf-8")
    print("\n" + report)
    print("\nWrote: communities_directed.csv, communities_undirected.csv, louvain_v3_summary.txt")


if __name__ == "__main__":
    main()
