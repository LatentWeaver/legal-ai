"""
Step 2 - Build a clean subset of the legal citation graph.

Filters out noise so downstream ranking only covers real case law:
  - Keep node_type == "case" only
  - Drop portal/statute/generic reference nodes by label pattern
  - Drop communities whose case ratio is below MIN_CASE_RATIO
  - Extract the induced subgraph and re-export

Input:  full_graph/betweenness/nodes.csv  (has all scores)
        full_graph/louvain_pagerank/graph.gexf  (for subgraph extraction)

Output (full_graph/clean/):
  nodes.csv      - clean node table with all scores
  graph.gexf     - clean directed subgraph for Gephi
  stats.txt      - summary
"""

import os
import re
import networkx as nx
import pandas as pd

# ── config ────────────────────────────────────────────────────────────────────

BW_NODES_CSV = os.path.join(os.path.dirname(__file__),
                             "full_graph", "betweenness", "nodes.csv")
GEXF_IN      = os.path.join(os.path.dirname(__file__),
                             "full_graph", "louvain_pagerank", "graph.gexf")
OUT_DIR      = os.path.join(os.path.dirname(__file__),
                             "full_graph", "clean")

# Communities where fewer than this fraction of nodes are source cases
# are treated as reference/noise clusters and excluded.
MIN_CASE_RATIO = 0.05

# Label patterns that indicate a node is a statute, portal, or generic ref.
# Applied as case-insensitive substring match.
NOISE_LABEL_PATTERNS = [
    r"indian kanoon",
    r"kanoon\.org",
    r"search engine for indian law",
    r"^article\s+\d",       # "Article 226", "Article 32", etc.
    r"^section\s+\d",       # "Section 302", etc.
    r"^s\.\d+",             # "S.100", "S.34A", etc.
    r"^the code of",        # "the Code of Civil Procedure"
    r"^code of ",
    r"^indian penal code$",
    r"^constitution of india$",
    r"^new$",               # bare "New" links
    r"^on board\s",
]

_noise_re = re.compile("|".join(NOISE_LABEL_PATTERNS), re.IGNORECASE)

def is_noise(label: str) -> bool:
    return bool(_noise_re.search(label.strip()))

# ── load scores ───────────────────────────────────────────────────────────────

print("Loading betweenness node table ...")
df = pd.read_csv(BW_NODES_CSV)
print(f"  {len(df):,} total nodes loaded")

# ── filter 1: source cases only ───────────────────────────────────────────────

df_cases = df[df["node_type"] == "case"].copy()
print(f"\nAfter node_type=case filter: {len(df_cases):,} nodes")

# ── filter 2: noisy labels ────────────────────────────────────────────────────

noise_mask = df_cases["label"].fillna("").apply(is_noise)
df_cases = df_cases[~noise_mask].copy()
print(f"After noise-label filter:    {len(df_cases):,} nodes "
      f"(removed {noise_mask.sum()} portal/statute nodes)")

# ── filter 3: low case-ratio communities ──────────────────────────────────────

# Compute case ratio per community from the FULL df (before case filter),
# so we get an accurate picture of each community's composition.
comm_total  = df.groupby("community_id").size().rename("total")
comm_cases  = df[df["node_type"] == "case"].groupby("community_id").size().rename("cases")
comm_ratio  = (comm_cases / comm_total).fillna(0).rename("case_ratio")

low_ratio_comms = set(comm_ratio[comm_ratio < MIN_CASE_RATIO].index.tolist())
print(f"\nCommunities with case_ratio < {MIN_CASE_RATIO:.0%}: "
      f"{len(low_ratio_comms)} communities excluded")

before = len(df_cases)
df_cases = df_cases[~df_cases["community_id"].isin(low_ratio_comms)].copy()
print(f"After community ratio filter: {len(df_cases):,} nodes "
      f"(removed {before - len(df_cases)})")

# ── filter 4: must have a real label ─────────────────────────────────────────

blank_mask = df_cases["label"].fillna("").str.strip().eq("")
df_cases = df_cases[~blank_mask].copy()
print(f"After blank-label filter:    {len(df_cases):,} nodes "
      f"(removed {blank_mask.sum()} unlabelled)")

# ── load graph and extract subgraph ──────────────────────────────────────────

print("\nLoading full graph for subgraph extraction ...")
G_full = nx.read_gexf(GEXF_IN)

clean_ids = set(df_cases["id"].astype(str).tolist())
G_clean   = G_full.subgraph(clean_ids).copy()
print(f"Clean subgraph: {G_clean.number_of_nodes():,} nodes | "
      f"{G_clean.number_of_edges():,} edges")

# ── community summary ─────────────────────────────────────────────────────────

summary_rows = []
for cid, grp in df_cases.groupby("community_id"):
    by_pr = grp.sort_values("global_pagerank", ascending=False)
    has_bw = "global_betweenness" in grp.columns

    row = {
        "community_id":    cid,
        "size":            len(grp),
        "top_by_pr":       by_pr["label"].iloc[0] if len(by_pr) else "",
        "top_global_pr":   round(by_pr["global_pagerank"].iloc[0], 8) if len(by_pr) else 0,
        "avg_in_degree":   round(grp["in_degree"].mean(), 1),
        "top5_labels":     " | ".join(by_pr["label"].head(5).tolist()),
    }
    if has_bw:
        by_bw = grp.sort_values("global_betweenness", ascending=False)
        row["top_by_betweenness"]    = by_bw["label"].iloc[0] if len(by_bw) else ""
        row["max_global_betweenness"] = round(by_bw["global_betweenness"].iloc[0], 10) if len(by_bw) else 0

    summary_rows.append(row)

summary_df = (
    pd.DataFrame(summary_rows)
    .sort_values("size", ascending=False)
    .reset_index(drop=True)
)

# ── export ────────────────────────────────────────────────────────────────────

os.makedirs(OUT_DIR, exist_ok=True)

nodes_csv = os.path.join(OUT_DIR, "nodes.csv")
df_cases.to_csv(nodes_csv, index=False)
print(f"\nWrote {nodes_csv}  ({len(df_cases):,} rows)")

summary_csv = os.path.join(OUT_DIR, "community_summary.csv")
summary_df.to_csv(summary_csv, index=False)
print(f"Wrote {summary_csv}  ({len(summary_df)} communities)")

gexf_out = os.path.join(OUT_DIR, "graph.gexf")
nx.write_gexf(G_clean, gexf_out)
print(f"Wrote {gexf_out}")

# ── stats ─────────────────────────────────────────────────────────────────────

top20 = df_cases.sort_values("global_pagerank", ascending=False).head(20)

lines = [
    f"Clean subset stats",
    f"------------------",
    f"Original nodes   : {len(df):,}",
    f"Clean case nodes : {len(df_cases):,}",
    f"Clean edges      : {G_clean.number_of_edges():,}",
    f"Communities kept : {df_cases['community_id'].nunique()}",
    f"Communities drop : {len(low_ratio_comms)} (case_ratio < {MIN_CASE_RATIO:.0%})",
    "",
    "Top 20 clean cases by global PageRank:",
] + [
    f"  {i+1:>3}. [PR={row['global_pagerank']:.6f}] "
    f"[in={row['in_degree']}] "
    f"[C{row['community_id']}] {row['label'][:70]}"
    for i, (_, row) in enumerate(top20.iterrows())
]

stats_path = os.path.join(OUT_DIR, "stats.txt")
with open(stats_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"Wrote {stats_path}")

print()
print("\n".join(lines))
print("\nDone.")
