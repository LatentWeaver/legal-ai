"""
Louvain community + sub-community detection — v2 (per Anshul's direction).

Changes from v1, following the email:
  * Runs on the DIRECT CITATION GRAPH (edge if one case cites the other),
    in both UNDIRECTED and DIRECTED views.
  * Sub-communities are NOT computed for every community. Instead we first
    pick the "top k communities that are significantly larger than the rest"
    and run second-level Louvain ONLY inside those.
  * Reports WHY each big community was selected, so the choice is defensible.

On the directed/undirected point (important, state this honestly):
  Louvain's modularity is defined for UNDIRECTED graphs. So:
    - UNDIRECTED view  -> Louvain directly (the standard, correct use).
    - DIRECTED view    -> we still run Louvain on the undirected projection
      for the partition, but ALSO report directional stats per community
      (internal in-/out-citations) so direction is used in interpretation.
  A fully directed partition needs Infomap or directed-modularity Leiden;
  this is left as the agreed next step pending the team's choice.

SCOPE: prototype on a bracket; point --json at the merged corpus later,
no code change.

OUTPUTS (next to input JSON):
  - communities_v2.csv        doc_id, name, community, subcommunity, in/out degree
  - louvain_v2_summary.txt     selection rationale + per-community detail

USAGE:
  python louvain_v2.py
  python louvain_v2.py --top-k 5            # force exactly 5 big communities
  python louvain_v2.py --select auto        # auto-pick via mean+std (default)
  python louvain_v2.py --json merged.json   # later, on the full graph

Dependencies: networkx
"""

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


# ── graphs ─────────────────────────────────────────────────────────────
def build_graphs(cases):
    """Directed graph DG (A->B = A cites B) and its undirected projection UG,
    both restricted to edges where both endpoints are in the corpus."""
    ids = {c["id"] for c in cases}
    DG = nx.DiGraph()
    DG.add_nodes_from(ids)
    for c in cases:
        for tgt in c["cites"]:
            if tgt in ids:
                DG.add_edge(c["id"], tgt)
    UG = DG.to_undirected()
    return DG, UG, ids


# ── top-k significant community selection ──────────────────────────────
def select_significant(communities, mode, top_k):
    """Return (indices_of_selected, rationale_string).

    communities: list sorted largest-first, each a set of nodes.
    mode 'auto' : pick communities whose size > mean + 1*std of sizes>1.
    mode 'topk' : pick the top_k largest.
    """
    real = [(i, c) for i, c in enumerate(communities) if len(c) > 1]
    sizes = [len(c) for _, c in real]
    if not sizes:
        return [], "No communities of size > 1."

    if mode == "topk":
        chosen = [i for i, _ in real[:top_k]]
        return chosen, f"Selected the {len(chosen)} largest communities (--top-k {top_k})."

    # auto: mean + 1 std
    mean = statistics.mean(sizes)
    std = statistics.pstdev(sizes) if len(sizes) > 1 else 0
    cutoff = mean + std
    chosen = [i for i, c in real if len(c) > cutoff]
    # guarantee at least the largest one
    if not chosen and real:
        chosen = [real[0][0]]
    rationale = (f"Auto-selected communities larger than mean+1std of community sizes.\n"
                 f"  community sizes (size>1): {sizes}\n"
                 f"  mean={mean:.1f}, std={std:.1f}, cutoff>{cutoff:.1f}\n"
                 f"  -> {len(chosen)} communities qualify as 'significantly larger'.")
    return chosen, rationale


# ── two-level with selective sub-clustering ────────────────────────────
def detect(UG, mode, top_k):
    communities = nx.community.louvain_communities(UG, seed=SEED)
    communities = sorted(communities, key=len, reverse=True)
    selected, rationale = select_significant(communities, mode, top_k)
    selected_set = set(selected)

    assignment = {}  # node -> (community_idx, subcommunity_idx)
    for ci, comm in enumerate(communities):
        if ci in selected_set and len(comm) >= 4:
            sub = UG.subgraph(comm)
            subcomms = sorted(
                nx.community.louvain_communities(sub, seed=SEED), key=len, reverse=True)
        else:
            subcomms = [comm]  # not sub-clustered -> single sub-group
        for si, sc in enumerate(subcomms):
            for node in sc:
                assignment[node] = (ci, si)
    for n in UG.nodes():
        assignment.setdefault(n, (-1, -1))
    return communities, selected, rationale, assignment


def main():
    ap = argparse.ArgumentParser(description="Louvain v2: top-k significant communities + sub-detection.")
    ap.add_argument("--json", type=Path, default=DEFAULT_JSON)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--select", choices=["auto", "topk"], default="auto",
                    help="how to pick 'significantly larger' communities (default auto = mean+std)")
    ap.add_argument("--top-k", type=int, default=5,
                    help="number of largest communities to sub-cluster when --select topk")
    args = ap.parse_args()

    if not args.json.exists():
        print(f"ERROR: file not found: {args.json}")
        return

    csv_names = load_csv_names(args.csv)
    cases = load_cases(args.json, csv_names)
    name = {c["id"]: c["name"] for c in cases}
    print(f"Loaded {len(cases)} cases")

    DG, UG, ids = build_graphs(cases)
    communities, selected, rationale, assignment = detect(UG, args.select, args.top_k)

    real = [c for c in communities if len(c) > 1]
    try:
        mod = nx.community.modularity(UG, communities)
    except Exception:
        mod = float("nan")

    # ── build report ──
    L = []
    L.append("=== LOUVAIN v2 — DIRECT CITATION GRAPH ===\n")
    L.append(f"Cases: {len(cases)}")
    L.append(f"Directed edges (A cites B): {DG.number_of_edges()}")
    L.append(f"Undirected edges: {UG.number_of_edges()}")
    connected = sum(1 for n in UG if UG.degree(n) > 0)
    L.append(f"Connected cases: {connected}   Isolated: {UG.number_of_nodes()-connected}")
    L.append(f"Top-level communities (size>1): {len(real)}")
    L.append(f"Undirected modularity: {mod:.4f}  (>0.3 = meaningful structure)\n")

    L.append("--- SELECTION OF 'SIGNIFICANTLY LARGER' COMMUNITIES ---")
    L.append(rationale + "\n")

    L.append("--- SELECTED COMMUNITIES (sub-clustered) ---")
    for ci in selected:
        comm = communities[ci]
        subs = {assignment[n][1] for n in comm}
        # directional stats: internal citations within this community
        sub_nodes = set(comm)
        internal = [(u, v) for u, v in DG.edges() if u in sub_nodes and v in sub_nodes]
        sample = "; ".join(name.get(x, x)[:30] for x in list(comm)[:3])
        L.append(f"  Community {ci}: {len(comm)} cases -> {len(subs)} sub-communities | "
                 f"{len(internal)} internal citations | e.g. {sample}")

    L.append("\n--- DIRECTIONAL NOTE ---")
    L.append("Partition computed on the undirected projection (Louvain requirement).")
    L.append("Directed structure is reported as internal citation counts per community;")
    L.append("a fully directed partition (Infomap / directed Leiden) is the agreed next step.")

    report = "\n".join(L)
    (args.json.parent / "louvain_v2_summary.txt").write_text(report, encoding="utf-8")
    print("\n" + report)

    # ── assignments CSV ──
    out = args.json.parent / "communities_v2.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["doc_id", "name", "community", "subcommunity",
                    "out_citations", "in_citations", "selected_for_subclustering"])
        for n in sorted(UG.nodes(), key=lambda x: assignment.get(x, (-1, -1))):
            comm, sub = assignment.get(n, (-1, -1))
            w.writerow([n, name.get(n, n), comm, sub,
                        DG.out_degree(n), DG.in_degree(n),
                        "yes" if comm in set(selected) else "no"])
    print(f"\nWrote: communities_v2.csv, louvain_v2_summary.txt")


if __name__ == "__main__":
    main()
