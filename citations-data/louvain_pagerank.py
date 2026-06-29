"""
Hybrid Louvain + PageRank community analysis on the citation graph.

Pipeline:
  1. Load directed graph from GEXF.
  2. Convert to undirected for Louvain (preserving edge weights as
     citation co-occurrence counts).
  3. Run Louvain community detection → macro community labels.
  4. Compute global PageRank on the directed graph → cross-community authority.
  5. For each Louvain community, compute within-community PageRank on the
     directed subgraph → local authority ranking.
  6. Export everything.

Output (citations-data/full_graph/louvain_pagerank/):
  nodes.csv           — full node table with community + PR scores
  community_summary.csv — per-community stats + top-5 nodes
  graph.gexf          — annotated graph for Gephi
  stats.txt           — overall summary
"""

import os
import networkx as nx
import pandas as pd
from community import community_louvain

# ── config ────────────────────────────────────────────────────────────────────

GEXF_IN  = os.path.join(os.path.dirname(__file__), "full_graph", "graph.gexf")
OUT_DIR  = os.path.join(os.path.dirname(__file__), "full_graph", "louvain_pagerank")
DAMPING  = 0.85
MAX_ITER = 100
TOL      = 1e-6

# ── 1. load ───────────────────────────────────────────────────────────────────

print("Loading graph ...")
G_dir = nx.read_gexf(GEXF_IN)
print(f"  {G_dir.number_of_nodes():,} nodes | {G_dir.number_of_edges():,} edges | directed={G_dir.is_directed()}")

def node_label(G, nid):
    return G.nodes[nid].get("label", nid)

# ── 2. undirected for Louvain ─────────────────────────────────────────────────

print("\nBuilding weighted undirected graph for Louvain ...")
G_undir = nx.Graph()
for u, v in G_dir.edges():
    if G_undir.has_edge(u, v):
        G_undir[u][v]["weight"] += 1
    else:
        G_undir.add_edge(u, v, weight=1)
# carry node attributes over
for nid, data in G_dir.nodes(data=True):
    if nid in G_undir:
        G_undir.nodes[nid].update(data)
print(f"  {G_undir.number_of_nodes():,} nodes | {G_undir.number_of_edges():,} edges")

# ── 3. Louvain community detection ────────────────────────────────────────────

print("\nRunning Louvain (resolution=1.0) ...")
partition = community_louvain.best_partition(G_undir, weight="weight",
                                             resolution=1.0, random_state=42)
n_communities = len(set(partition.values()))
print(f"  {n_communities} communities found")

# ── 4. global PageRank ────────────────────────────────────────────────────────

print("\nComputing global PageRank ...")
global_pr = nx.pagerank(G_dir, alpha=DAMPING, max_iter=MAX_ITER, tol=TOL)
print(f"  max={max(global_pr.values()):.6f} | min={min(global_pr.values()):.6f}")

# ── 5. within-community PageRank ─────────────────────────────────────────────

print("\nComputing within-community PageRank ...")

# group nodes by community
comm_nodes: dict[int, list] = {}
for nid, cid in partition.items():
    comm_nodes.setdefault(cid, []).append(nid)

local_pr:   dict[str, float] = {}
local_rank: dict[str, int]   = {}

for cid, members in comm_nodes.items():
    sub = G_dir.subgraph(members)
    if sub.number_of_edges() == 0:
        # isolated nodes — assign uniform score
        for nid in members:
            local_pr[nid]   = 1.0 / len(members)
            local_rank[nid] = 1
        continue
    pr = nx.pagerank(sub, alpha=DAMPING, max_iter=MAX_ITER, tol=TOL)
    ranked = sorted(pr.items(), key=lambda x: x[1], reverse=True)
    for rank, (nid, score) in enumerate(ranked, 1):
        local_pr[nid]   = score
        local_rank[nid] = rank

print(f"  done ({len(local_pr):,} nodes assigned local PR)")

# ── 6. build node table ───────────────────────────────────────────────────────

in_deg  = dict(G_dir.in_degree())
out_deg = dict(G_dir.out_degree())

rows = []
for nid, data in G_dir.nodes(data=True):
    cid = partition.get(nid, -1)
    rows.append({
        "id":                  nid,
        "label":               data.get("label", ""),
        "year":                data.get("year", ""),
        "url":                 data.get("url", ""),
        "node_type":           data.get("node_type", "reference"),
        "community_id":        cid,
        "global_pagerank":     round(global_pr.get(nid, 0), 8),
        "local_pagerank":      round(local_pr.get(nid, 0),  8),
        "local_rank":          local_rank.get(nid, -1),
        "in_degree":           in_deg.get(nid, 0),
        "out_degree":          out_deg.get(nid, 0),
        "community_size":      len(comm_nodes.get(cid, [])),
    })

nodes_df = pd.DataFrame(rows)

# ── 7. community summary ──────────────────────────────────────────────────────

summary_rows = []
for cid, members in comm_nodes.items():
    sub_df = nodes_df[nodes_df["community_id"] == cid].sort_values(
        "global_pagerank", ascending=False
    )
    top5_global = " | ".join(sub_df["label"].head(5).tolist())
    summary_rows.append({
        "community_id":        cid,
        "size":                len(members),
        "top_node_label":      sub_df["label"].iloc[0] if len(sub_df) else "",
        "top_node_global_pr":  sub_df["global_pagerank"].iloc[0] if len(sub_df) else 0,
        "top5_by_global_pr":   top5_global,
        "case_nodes":          int((sub_df["node_type"] == "case").sum()),
        "ref_nodes":           int((sub_df["node_type"] == "reference").sum()),
    })

summary_df = (
    pd.DataFrame(summary_rows)
    .sort_values("size", ascending=False)
    .reset_index(drop=True)
)

# ── 8. annotate directed graph + write GEXF ───────────────────────────────────

for nid in G_dir.nodes():
    G_dir.nodes[nid]["community_id"]    = partition.get(nid, -1)
    G_dir.nodes[nid]["global_pagerank"] = round(global_pr.get(nid, 0), 8)
    G_dir.nodes[nid]["local_pagerank"]  = round(local_pr.get(nid, 0),  8)
    G_dir.nodes[nid]["local_rank"]      = local_rank.get(nid, -1)

# ── 9. export ─────────────────────────────────────────────────────────────────

os.makedirs(OUT_DIR, exist_ok=True)

nodes_csv = os.path.join(OUT_DIR, "nodes.csv")
nodes_df.to_csv(nodes_csv, index=False)
print(f"\nWrote {nodes_csv}  ({len(nodes_df):,} rows)")

summary_csv = os.path.join(OUT_DIR, "community_summary.csv")
summary_df.to_csv(summary_csv, index=False)
print(f"Wrote {summary_csv}  ({len(summary_df)} communities)")

gexf_out = os.path.join(OUT_DIR, "graph.gexf")
nx.write_gexf(G_dir, gexf_out)
print(f"Wrote {gexf_out}")

# ── 10. stats ─────────────────────────────────────────────────────────────────

top20_global = nodes_df.sort_values("global_pagerank", ascending=False).head(20)

lines = [
    f"Total nodes      : {G_dir.number_of_nodes():,}",
    f"Total edges      : {G_dir.number_of_edges():,}",
    f"Communities      : {n_communities}",
    f"Largest community: {summary_df['size'].iloc[0]:,} nodes "
    f"(C{summary_df['community_id'].iloc[0]})",
    f"Smallest         : {summary_df['size'].iloc[-1]:,} nodes",
    "",
    "Top 20 by global PageRank:",
] + [
    f"  {i+1:>3}. [PR={row['global_pagerank']:.6f}] "
    f"[C{row['community_id']}] {row['label'][:70]}"
    for i, (_, row) in enumerate(top20_global.iterrows())
] + [
    "",
    "Top 20 communities by size:",
] + [
    f"  C{row['community_id']:>4} | {row['size']:>6} nodes "
    f"| cases={row['case_nodes']:>4} | top: {row['top_node_label'][:55]}"
    for _, row in summary_df.head(20).iterrows()
]

stats_path = os.path.join(OUT_DIR, "stats.txt")
with open(stats_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"Wrote {stats_path}")

print()
print("\n".join(lines))
print("\nDone.")
