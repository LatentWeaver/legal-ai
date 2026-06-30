"""
Betweenness centrality analysis on the legal citation graph.

Betweenness answers: which cases act as bridges - the shortest path between
two otherwise unconnected cases must pass through them. High betweenness cases
are cross-doctrine connectors and structural linchpins, complementing the
authority signal from PageRank.

Pipeline:
  1. Load directed graph from louvain_pagerank/graph.gexf
     (already has community_id + global_pagerank from prior run).
  2. Compute approximate global betweenness on the full directed graph
     using k-sample approximation (k=500, ~4% error, tractable runtime).
  3. For each Louvain community, compute within-community betweenness
     (exact for small communities, approximate for large ones).
  4. Compute bridge_score = global_betweenness - local_betweenness_scaled,
     which surfaces cross-community connectors vs local structural linchpins.
  5. Export everything.

Output (citations-data/full_graph/betweenness/):
  nodes.csv            - full node table with betweenness scores
  community_summary.csv - per-community top bridges + stats
  graph.gexf           - annotated graph for Gephi
  stats.txt            - overall summary
"""

import os
import networkx as nx
import pandas as pd

# ── config ────────────────────────────────────────────────────────────────────

GEXF_IN  = os.path.join(os.path.dirname(__file__),
                         "full_graph", "louvain_pagerank", "graph.gexf")
OUT_DIR  = os.path.join(os.path.dirname(__file__),
                         "full_graph", "betweenness")

# k for approximation: higher = more accurate but slower.
# k=500 gives ~4% error; k=1000 gives ~3% error.
K_GLOBAL = 500

# Communities larger than this get approximate within-community betweenness;
# smaller ones get exact.
LARGE_COMMUNITY_THRESHOLD = 800
K_LOCAL = 200  # k for large-community approximation

# ── 1. load ───────────────────────────────────────────────────────────────────

print("Loading graph from louvain_pagerank output ...")
G_dir = nx.read_gexf(GEXF_IN)
print(f"  {G_dir.number_of_nodes():,} nodes | "
      f"{G_dir.number_of_edges():,} edges | "
      f"directed={G_dir.is_directed()}")

# ── 2. global betweenness (approximate) ──────────────────────────────────────

print(f"\nComputing global betweenness (k={K_GLOBAL} samples, approximate) ...")
print("  This may take several minutes on 124K nodes ...")
global_bw = nx.betweenness_centrality(
    G_dir, k=K_GLOBAL, normalized=True, seed=42
)
print(f"  max={max(global_bw.values()):.6f} | "
      f"non-zero={sum(1 for v in global_bw.values() if v > 0):,}")

# ── 3. community grouping ─────────────────────────────────────────────────────

print("\nGrouping nodes by community ...")
comm_nodes: dict[int, list] = {}
for nid, data in G_dir.nodes(data=True):
    cid = int(data.get("community_id", -1))
    comm_nodes.setdefault(cid, []).append(nid)

print(f"  {len(comm_nodes)} communities | "
      f"largest={max(len(v) for v in comm_nodes.values()):,} nodes")

# ── 4. within-community betweenness ──────────────────────────────────────────

print("\nComputing within-community betweenness ...")
local_bw:   dict[str, float] = {}
local_bw_rank: dict[str, int] = {}

for cid, members in comm_nodes.items():
    sub = G_dir.subgraph(members)
    if sub.number_of_edges() == 0:
        for nid in members:
            local_bw[nid] = 0.0
            local_bw_rank[nid] = 1
        continue

    if len(members) > LARGE_COMMUNITY_THRESHOLD:
        bw = nx.betweenness_centrality(
            sub, k=min(K_LOCAL, len(members)), normalized=True, seed=42
        )
    else:
        bw = nx.betweenness_centrality(sub, normalized=True)

    ranked = sorted(bw.items(), key=lambda x: x[1], reverse=True)
    for rank, (nid, score) in enumerate(ranked, 1):
        local_bw[nid] = score
        local_bw_rank[nid] = rank

print(f"  done ({len(local_bw):,} nodes assigned local betweenness)")

# ── 5. bridge score ───────────────────────────────────────────────────────────
#
# A pure cross-community connector has high global_bw but low local_bw.
# bridge_score > 0  => more of a cross-community bridge
# bridge_score < 0  => more of a local structural linchpin
#
# We normalise local_bw to the same scale as global_bw using community size,
# then take the difference. This is a heuristic; absolute values matter less
# than relative ranking.

print("\nComputing bridge scores ...")

def _bridge_score(gbw: float, lbw: float, comm_size: int, total_nodes: int) -> float:
    # local_bw is normalised within (n-1)(n-2) of the subgraph.
    # Scale it down to global magnitude so we can compare directly.
    scale = (comm_size / total_nodes) ** 2
    return gbw - lbw * scale

total_n = G_dir.number_of_nodes()

# ── 6. build node table ───────────────────────────────────────────────────────

in_deg  = dict(G_dir.in_degree())
out_deg = dict(G_dir.out_degree())

rows = []
for nid, data in G_dir.nodes(data=True):
    cid      = int(data.get("community_id", -1))
    c_size   = len(comm_nodes.get(cid, []))
    gbw      = global_bw.get(nid, 0.0)
    lbw      = local_bw.get(nid, 0.0)
    bs       = _bridge_score(gbw, lbw, c_size, total_n)

    rows.append({
        "id":                   nid,
        "label":                data.get("label", ""),
        "year":                 data.get("year", ""),
        "url":                  data.get("url", ""),
        "node_type":            data.get("node_type", "reference"),
        "community_id":         cid,
        "community_size":       c_size,
        "global_pagerank":      round(float(data.get("global_pagerank", 0)), 8),
        "global_betweenness":   round(gbw, 10),
        "local_betweenness":    round(lbw, 10),
        "local_bw_rank":        local_bw_rank.get(nid, -1),
        "bridge_score":         round(bs, 10),
        "in_degree":            in_deg.get(nid, 0),
        "out_degree":           out_deg.get(nid, 0),
    })

nodes_df = pd.DataFrame(rows)

# ── 7. community summary ──────────────────────────────────────────────────────

summary_rows = []
for cid, members in comm_nodes.items():
    sub_df = nodes_df[nodes_df["community_id"] == cid]

    by_global_bw = sub_df.sort_values("global_betweenness", ascending=False)
    by_local_bw  = sub_df.sort_values("local_betweenness",  ascending=False)
    by_bridge    = sub_df.sort_values("bridge_score",        ascending=False)

    top_global_bridge = by_bridge["label"].iloc[0] if len(by_bridge) else ""
    top3_local_bw     = " | ".join(by_local_bw["label"].head(3).tolist())
    top3_global_bw    = " | ".join(by_global_bw["label"].head(3).tolist())

    summary_rows.append({
        "community_id":             cid,
        "size":                     len(members),
        "top_cross_community_bridge": top_global_bridge,
        "top3_by_global_bw":        top3_global_bw,
        "top3_by_local_bw":         top3_local_bw,
        "avg_global_bw":            round(sub_df["global_betweenness"].mean(), 10),
        "max_global_bw":            round(sub_df["global_betweenness"].max(), 10),
        "max_local_bw":             round(sub_df["local_betweenness"].max(), 10),
        "case_nodes":               int((sub_df["node_type"] == "case").sum()),
    })

summary_df = (
    pd.DataFrame(summary_rows)
    .sort_values("size", ascending=False)
    .reset_index(drop=True)
)

# ── 8. annotate directed graph for Gephi ──────────────────────────────────────

for nid in G_dir.nodes():
    G_dir.nodes[nid]["global_betweenness"] = round(global_bw.get(nid, 0.0), 10)
    G_dir.nodes[nid]["local_betweenness"]  = round(local_bw.get(nid, 0.0),  10)
    G_dir.nodes[nid]["local_bw_rank"]      = local_bw_rank.get(nid, -1)
    G_dir.nodes[nid]["bridge_score"]       = round(
        _bridge_score(
            global_bw.get(nid, 0.0),
            local_bw.get(nid, 0.0),
            len(comm_nodes.get(int(G_dir.nodes[nid].get("community_id", -1)), [])),
            total_n,
        ), 10
    )

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

top20_global_bw = nodes_df.sort_values("global_betweenness", ascending=False).head(20)
top20_bridge    = nodes_df.sort_values("bridge_score",        ascending=False).head(20)
top20_local_bw  = nodes_df.sort_values("local_betweenness",  ascending=False).head(20)

lines = [
    f"Total nodes       : {G_dir.number_of_nodes():,}",
    f"Total edges       : {G_dir.number_of_edges():,}",
    f"Communities       : {len(comm_nodes)}",
    f"K global samples  : {K_GLOBAL}",
    f"K local threshold : >{LARGE_COMMUNITY_THRESHOLD} nodes → k={K_LOCAL}",
    "",
    "Top 20 by global betweenness (highest cross-graph flow):",
] + [
    f"  {i+1:>3}. [BW={row['global_betweenness']:.8f}] "
    f"[PR={row['global_pagerank']:.6f}] "
    f"[C{row['community_id']}] {row['label'][:65]}"
    for i, (_, row) in enumerate(top20_global_bw.iterrows())
] + [
    "",
    "Top 20 cross-community bridges (high global, low local betweenness):",
] + [
    f"  {i+1:>3}. [bridge={row['bridge_score']:.8f}] "
    f"[BW={row['global_betweenness']:.8f}] "
    f"[C{row['community_id']}] {row['label'][:60]}"
    for i, (_, row) in enumerate(top20_bridge.iterrows())
] + [
    "",
    "Top 20 by local betweenness (structural linchpins within community):",
] + [
    f"  {i+1:>3}. [local_bw={row['local_betweenness']:.8f}] "
    f"[rank={row['local_bw_rank']}] "
    f"[C{row['community_id']}] {row['label'][:60]}"
    for i, (_, row) in enumerate(top20_local_bw.iterrows())
]

stats_path = os.path.join(OUT_DIR, "stats.txt")
with open(stats_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"Wrote {stats_path}")

print()
print("\n".join(lines))
print("\nDone.")
