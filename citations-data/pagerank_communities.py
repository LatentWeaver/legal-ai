"""
PageRank-based community detection on the citation graph.

Approach — Personalized PageRank (PPR) communities:
  1. Compute standard PageRank → authority score for every node.
  2. Pick the top-K seeds (highest PageRank nodes) as community anchors.
  3. Run Personalized PageRank from each seed (teleport vector concentrated
     on that seed). Each node gets a PPR score toward every seed.
  4. Assign each node to the seed it is most "attracted" to → community label.

Why PPR instead of Louvain:
  - Louvain maximises modularity on an undirected graph; it ignores edge
    direction and treats all edges as equal.
  - PPR respects the *direction* of citations and the *authority* of nodes.
    A case pulled toward a landmark judgment (Kesavananda, Puttaswamy …)
    through many paths ends up in that judgment's community.

Output (citations-data/full_graph/pagerank/):
  pagerank_nodes.csv   — id, label, year, url, node_type, pagerank,
                         in_degree, out_degree, community_id, community_seed
  community_summary.csv — community_id, seed_label, seed_pagerank, size,
                           top5_members
  graph_pr.gexf        — GEXF with pagerank + community_id attributes
"""

import os
import networkx as nx
import pandas as pd

# ── config ────────────────────────────────────────────────────────────────────

GEXF_IN  = os.path.join(os.path.dirname(__file__), "full_graph", "graph.gexf")
OUT_DIR  = os.path.join(os.path.dirname(__file__), "full_graph", "pagerank")

NUM_SEEDS    = 30    # number of community anchors (only real case nodes)

# Labels that are site/placeholder noise — excluded from seed selection
_NOISE_LABELS = {"indian kanoon - search engine for indian law"}
DAMPING      = 0.85  # standard PageRank damping factor
MAX_ITER     = 100
TOL          = 1e-6

# ── load ──────────────────────────────────────────────────────────────────────

print("Loading graph …")
G = nx.read_gexf(GEXF_IN)
print(f"  {G.number_of_nodes():,} nodes  |  {G.number_of_edges():,} edges  |  directed={G.is_directed()}")

# ── step 1: standard PageRank ─────────────────────────────────────────────────

print(f"\nComputing PageRank (alpha={DAMPING}) …")
pr = nx.pagerank(G, alpha=DAMPING, max_iter=MAX_ITER, tol=TOL)
print(f"  done. max PR = {max(pr.values()):.6f}  |  min = {min(pr.values()):.6f}")

# ── step 2: pick top-K seeds ──────────────────────────────────────────────────

sorted_pr = sorted(pr.items(), key=lambda x: x[1], reverse=True)

def node_label(nid):
    return G.nodes[nid].get("label", nid)

# Filter out placeholder/noise labels; only pick real case nodes as anchors
seeds = [
    nid for nid, _ in sorted_pr
    if node_label(nid).lower() not in _NOISE_LABELS
       and G.nodes[nid].get("node_type") == "case"
][:NUM_SEEDS]

print(f"\nTop {NUM_SEEDS} seed nodes (community anchors):")
for rank, nid in enumerate(seeds, 1):
    print(f"  {rank:>3}. [{pr[nid]:.6f}] {node_label(nid)[:70]}")

# ── step 3: personalized PageRank per seed ────────────────────────────────────

print(f"\nRunning Personalized PageRank from each of {NUM_SEEDS} seeds …")

# ppr_matrix[node] = {seed_id: ppr_score}
# We only store the winning seed per node to stay memory-efficient.
best_seed  = {}   # node → seed_id with highest PPR
best_score = {}   # node → that PPR score

for i, seed in enumerate(seeds):
    personalization = {seed: 1.0}
    ppr = nx.pagerank(G, alpha=DAMPING, personalization=personalization,
                      max_iter=MAX_ITER, tol=TOL)
    for node, score in ppr.items():
        if node not in best_score or score > best_score[node]:
            best_score[node] = score
            best_seed[node]  = seed
    print(f"  [{i+1:>2}/{NUM_SEEDS}] seed: {node_label(seed)[:60]}")

# nodes not assigned (shouldn't happen, but guard)
seed_map = {s: i for i, s in enumerate(seeds)}
for nid in G.nodes():
    if nid not in best_seed:
        best_seed[nid]  = seeds[0]
        best_score[nid] = 0.0

# ── step 4: build output data ─────────────────────────────────────────────────

in_deg  = dict(G.in_degree())
out_deg = dict(G.out_degree())

rows = []
for nid, data in G.nodes(data=True):
    seed_id = best_seed[nid]
    rows.append({
        "id":            nid,
        "label":         data.get("label", ""),
        "year":          data.get("year", ""),
        "url":           data.get("url", ""),
        "node_type":     data.get("node_type", "reference"),
        "pagerank":      round(pr[nid], 8),
        "in_degree":     in_deg.get(nid, 0),
        "out_degree":    out_deg.get(nid, 0),
        "community_id":  seed_map[seed_id],
        "community_seed": node_label(seed_id),
    })

nodes_df = pd.DataFrame(rows)

# ── community summary ─────────────────────────────────────────────────────────

comm_summary = []
for i, seed in enumerate(seeds):
    members = nodes_df[nodes_df["community_id"] == i].sort_values("pagerank", ascending=False)
    top5    = " | ".join(members["label"].head(5).tolist())
    comm_summary.append({
        "community_id":    i,
        "seed_id":         seed,
        "seed_label":      node_label(seed),
        "seed_pagerank":   round(pr[seed], 8),
        "size":            len(members),
        "top5_members":    top5,
    })

summary_df = pd.DataFrame(comm_summary).sort_values("size", ascending=False)

# ── annotate graph + write GEXF ───────────────────────────────────────────────

for nid in G.nodes():
    G.nodes[nid]["pagerank"]     = round(pr[nid], 8)
    G.nodes[nid]["community_id"] = seed_map[best_seed[nid]]

# ── export ────────────────────────────────────────────────────────────────────

os.makedirs(OUT_DIR, exist_ok=True)

nodes_csv = os.path.join(OUT_DIR, "pagerank_nodes.csv")
nodes_df.to_csv(nodes_csv, index=False)
print(f"\nWrote {nodes_csv}  ({len(nodes_df):,} rows)")

summary_csv = os.path.join(OUT_DIR, "community_summary.csv")
summary_df.to_csv(summary_csv, index=False)
print(f"Wrote {summary_csv}  ({len(summary_df)} communities)")

gexf_out = os.path.join(OUT_DIR, "graph_pr.gexf")
nx.write_gexf(G, gexf_out)
print(f"Wrote {gexf_out}")

# ── print summary ─────────────────────────────────────────────────────────────

print("\n-- Community summary (by size) --")
for _, row in summary_df.iterrows():
    print(f"  C{row['community_id']:>2} [{row['size']:>6} nodes] "
          f"[PR={row['seed_pagerank']:.5f}] {row['seed_label'][:60]}")

print("\nDone.")
