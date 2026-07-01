"""
Two-level Louvain community + sub-community detection - common-schema version.
Reads the COMMON schema (case/year/url/precedents:[{case,url}]) used by all brackets and the merged corpus. Node ID is the Indian Kanoon doc number from the url. Case names come from the 'case' field (no CSV needed).
Runs Louvain on the direct citation graph, DIRECTED and UNDIRECTED (NetworkX uses the directed modularity gain, Dugue & Perez, on a DiGraph). Picks the communities significantly larger than the rest and sub-clusters those.
Follows the lab's NARRA-SCALE two-level pattern.

USAGE:
  python louvain_v3.py --json citations_merged.json
  python louvain_v3.py --json citations_merged.json --select topk --top-k 5
"""

import argparse
import csv
import json
import re
import statistics
from pathlib import Path

import networkx as nx

SEED = 42


def doc_id(url):
    m = re.search(r"/doc/(\d+)/", url or "")
    return m.group(1) if m else None


def load_cases(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for rec in data:
        sid = doc_id(rec.get("url", ""))
        if not sid:
            continue
        cites = [doc_id(p.get("url", "")) for p in rec.get("precedents", [])]
        cites = [c for c in cites if c]
        cases.append({
            "id": sid,
            "name": (rec.get("case") or sid).replace("_", " ").strip(),
            "cites": cites,
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


def select_significant(communities, mode, top_k):
    real = [(i, c) for i, c in enumerate(communities) if len(c) > 1]
    sizes = [len(c) for _, c in real]
    if not sizes:
        return [], "No communities of size > 1."
    if mode == "topk":
        chosen = [i for i, _ in real[:top_k]]
        return chosen, "Selected the %d largest communities (--top-k %d)." % (len(chosen), top_k)
    mean = statistics.mean(sizes)
    std = statistics.pstdev(sizes) if len(sizes) > 1 else 0
    cutoff = mean + std
    chosen = [i for i, c in real if len(c) > cutoff]
    if not chosen and real:
        chosen = [real[0][0]]
    rationale = ("Auto: communities larger than mean+1std.\n"
                 "  sizes(>1): %s%s\n"
                 "  mean=%.1f std=%.1f cutoff>%.1f\n"
                 "  -> %d significantly-larger communities sub-clustered."
                 % (sizes[:40], " ..." if len(sizes) > 40 else "", mean, std, cutoff, len(chosen)))
    return chosen, rationale


def two_level(G, mode, top_k):
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
        w.writerow(["doc_id", "case", "community", "subcommunity", "out_citations", "in_citations"])
        for n in sorted(DG.nodes(), key=lambda x: assignment.get(x, (-1, -1))):
            comm, sub = assignment.get(n, (-1, -1))
            w.writerow([n, name.get(n, n), comm, sub, DG.out_degree(n), DG.in_degree(n)])


def summarize(label, G, communities, selected, rationale, assignment, name):
    L = ["=== %s ===" % label]
    L.append("Nodes: %d  Edges: %d" % (G.number_of_nodes(), G.number_of_edges()))
    real = [c for c in communities if len(c) > 1]
    L.append("Communities (size>1): %d   Modularity: %.4f" % (len(real), modularity_of(G, communities)))
    connected = sum(1 for n in G if G.degree(n) > 0)
    L.append("Connected: %d   Isolated: %d" % (connected, G.number_of_nodes() - connected))
    L.append(rationale)
    L.append("Selected (sub-clustered) communities:")
    for ci in selected:
        comm = communities[ci]
        subs = {assignment[n][1] for n in comm}
        sample = "; ".join(name.get(x, x)[:28] for x in list(comm)[:3])
        L.append("  C%d: %d cases -> %d subs | e.g. %s" % (ci, len(comm), len(subs), sample))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Two-level Louvain on common-schema citation data.")
    ap.add_argument("--json", type=Path, default=Path("citations_merged.json"))
    ap.add_argument("--select", choices=["auto", "topk"], default="auto")
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()

    if not args.json.exists():
        print("ERROR: file not found: %s" % args.json)
        return

    cases = load_cases(args.json)
    name = {c["id"]: c["name"] for c in cases}
    print("Loaded %d cases from %s" % (len(cases), args.json.name))

    DG = build_directed_graph(cases)
    UG = DG.to_undirected()

    d_comms, d_sel, d_rat, d_asg = two_level(DG, args.select, args.top_k)
    write_assignments(args.json.parent / "communities_directed.csv", DG, d_asg, name)
    d_sum = summarize("DIRECTED CITATION GRAPH (directed modularity)", DG, d_comms, d_sel, d_rat, d_asg, name)

    u_comms, u_sel, u_rat, u_asg = two_level(UG, args.select, args.top_k)
    write_assignments(args.json.parent / "communities_undirected.csv", DG, u_asg, name)
    u_sum = summarize("UNDIRECTED CITATION GRAPH (standard modularity)", UG, u_comms, u_sel, u_rat, u_asg, name)

    d_real = len([c for c in d_comms if len(c) > 1])
    u_real = len([c for c in u_comms if len(c) > 1])
    cmp = [
        "=== DIRECTED vs UNDIRECTED COMPARISON ===",
        "Directed   : %d communities, modularity %.4f" % (d_real, modularity_of(DG, d_comms)),
        "Undirected : %d communities, modularity %.4f" % (u_real, modularity_of(UG, u_comms)),
        "",
        "Method follows the lab's NARRA-SCALE two-level pattern. Per Anshul,",
        "undirected is the primary run; directed is included for comparison.",
    ]
    report = "\n\n".join([d_sum, u_sum, "\n".join(cmp)])
    (args.json.parent / "louvain_v3_summary.txt").write_text(report, encoding="utf-8")
    print("\n" + report)
    print("\nWrote: communities_directed.csv, communities_undirected.csv, louvain_v3_summary.txt")


if __name__ == "__main__":
    main()
