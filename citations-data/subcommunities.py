"""
Step 3 - Subcommunity detection within large legal clusters.

The three largest active-case communities (C0, C2, C4) each contain
7K-11K nodes and likely span multiple distinct legal sub-doctrines.
This script re-runs Louvain at higher resolution on each of them
to expose the internal sub-structure.

Input:  full_graph/clean/nodes.csv
        full_graph/clean/graph.gexf

Output (full_graph/subcommunities/):
  nodes.csv             - nodes with subcommunity_id + all prior scores
  subcommunity_summary.csv
  subgraphs/<cid>/      - per-community GEXF for Gephi
  stats.txt
"""

import os
import networkx as nx
import pandas as pd
from networkx.algorithms.community import louvain_communities

# ── config ────────────────────────────────────────────────────────────────────

CLEAN_NODES = os.path.join(os.path.dirname(__file__),
                            "full_graph", "clean", "nodes.csv")
CLEAN_GEXF  = os.path.join(os.path.dirname(__file__),
                            "full_graph", "clean", "graph.gexf")
OUT_DIR     = os.path.join(os.path.dirname(__file__),
                            "full_graph", "subcommunities")

# Communities to drill into - the large active-case clusters.
# Extend this list if you want subcommunity detection on more communities.
TARGET_COMMUNITIES = [0, 2, 4, 5, 8]

# Louvain resolution for subcommunity pass - higher = more, smaller communities.
SUBCOMM_RESOLUTION = 1.5

# ── load ──────────────────────────────────────────────────────────────────────

print("Loading clean graph ...")
G = nx.read_gexf(CLEAN_GEXF)
print(f"  {G.number_of_nodes():,} nodes | {G.number_of_edges():,} edges")

df = pd.read_csv(CLEAN_NODES)
df["id"] = df["id"].astype(str)
print(f"  {len(df):,} clean case nodes loaded")

# ── per-community subcommunity detection ─────────────────────────────────────

print(f"\nTarget communities: {TARGET_COMMUNITIES}")
print(f"Louvain resolution: {SUBCOMM_RESOLUTION}\n")

# subcommunity_id will be a string like "C2_S3" (community 2, sub 3)
df["subcommunity_id"] = df["community_id"].astype(str).apply(lambda c: f"C{c}")

all_sub_rows = []

for cid in TARGET_COMMUNITIES:
    members_df = df[df["community_id"] == cid]
    member_ids = set(members_df["id"].tolist())

    if len(member_ids) < 10:
        print(f"  C{cid}: only {len(member_ids)} nodes - skipping")
        continue

    sub_dir = G.subgraph(member_ids)
    sub_undir = nx.Graph()
    for u, v in sub_dir.edges():
        if sub_undir.has_edge(u, v):
            sub_undir[u][v]["weight"] += 1
        else:
            sub_undir.add_edge(u, v, weight=1)
    for nid, data in sub_dir.nodes(data=True):
        if nid in sub_undir:
            sub_undir.nodes[nid].update(data)

    if sub_undir.number_of_edges() == 0:
        print(f"  C{cid}: no edges in subgraph - skipping")
        continue

    communities_list = louvain_communities(
        sub_undir, weight="weight", resolution=SUBCOMM_RESOLUTION, seed=42
    )
    # Build node -> sub_id dict from list of sets
    partition = {}
    for sub_id, comm_set in enumerate(communities_list):
        for nid in comm_set:
            partition[nid] = sub_id

    n_sub = len(communities_list)
    print(f"  C{cid}: {len(member_ids):,} nodes -> {n_sub} subcommunities")

    # Assign subcommunity label back to df
    for nid, sub_id in partition.items():
        label = f"C{cid}_S{sub_id}"
        df.loc[df["id"] == nid, "subcommunity_id"] = label

    # Build per-subcommunity summary
    sub_df = members_df.copy()
    for nid, sub_id in partition.items():
        sub_df.loc[sub_df["id"] == nid, "_sub"] = sub_id

    for sub_id, grp in sub_df.groupby("_sub"):
        by_pr = grp.sort_values("global_pagerank", ascending=False)
        top5  = " | ".join(by_pr["label"].head(5).tolist())
        row = {
            "parent_community":  cid,
            "subcommunity_id":   f"C{cid}_S{sub_id}",
            "size":              len(grp),
            "top_label":         by_pr["label"].iloc[0] if len(by_pr) else "",
            "top_global_pr":     round(by_pr["global_pagerank"].iloc[0], 8) if len(by_pr) else 0,
            "top5_labels":       top5,
            "avg_in_degree":     round(grp["in_degree"].mean(), 1),
        }
        if "global_betweenness" in grp.columns:
            by_bw = grp.sort_values("global_betweenness", ascending=False)
            row["top_betweenness_label"] = by_bw["label"].iloc[0] if len(by_bw) else ""
        all_sub_rows.append(row)

    # Write per-community subgraph GEXF
    sub_gexf_dir = os.path.join(OUT_DIR, "subgraphs", f"C{cid}")
    os.makedirs(sub_gexf_dir, exist_ok=True)

    sub_dir_copy = G.subgraph(member_ids).copy()
    for nid in sub_dir_copy.nodes():
        sub_dir_copy.nodes[nid]["subcommunity_id"] = df.loc[
            df["id"] == nid, "subcommunity_id"
        ].values[0] if nid in df["id"].values else f"C{cid}"

    nx.write_gexf(sub_dir_copy, os.path.join(sub_gexf_dir, "graph.gexf"))
    print(f"    Wrote subgraph GEXF for C{cid}")

# ── export ────────────────────────────────────────────────────────────────────

os.makedirs(OUT_DIR, exist_ok=True)

nodes_csv = os.path.join(OUT_DIR, "nodes.csv")
df.to_csv(nodes_csv, index=False)
print(f"\nWrote {nodes_csv}  ({len(df):,} rows)")

if all_sub_rows:
    summary_df = (
        pd.DataFrame(all_sub_rows)
        .sort_values(["parent_community", "size"], ascending=[True, False])
        .reset_index(drop=True)
    )
    summary_csv = os.path.join(OUT_DIR, "subcommunity_summary.csv")
    summary_df.to_csv(summary_csv, index=False)
    print(f"Wrote {summary_csv}  ({len(summary_df)} subcommunities)")

# ── stats ─────────────────────────────────────────────────────────────────────

lines = [
    "Subcommunity Detection Results",
    "==============================",
    f"Target communities : {TARGET_COMMUNITIES}",
    f"Resolution         : {SUBCOMM_RESOLUTION}",
    "",
]

if all_sub_rows:
    summary_df = pd.DataFrame(all_sub_rows)
    for cid in TARGET_COMMUNITIES:
        sub = summary_df[summary_df["parent_community"] == cid].sort_values(
            "size", ascending=False
        )
        if sub.empty:
            continue
        lines.append(f"C{cid} -> {len(sub)} subcommunities:")
        for _, row in sub.iterrows():
            lines.append(
                f"  {row['subcommunity_id']:>10} | {row['size']:>5} nodes "
                f"| top: {row['top_label'][:60]}"
            )
        lines.append("")

stats_path = os.path.join(OUT_DIR, "stats.txt")
with open(stats_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"Wrote {stats_path}")
print()
print("\n".join(lines))
print("Done.")
