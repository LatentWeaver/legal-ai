"""
Step 4 - Composite importance ranking of legal cases.

Combines three orthogonal signals into a single importance score:
  - global_pagerank       : citation authority (who is cited by important cases)
  - global_betweenness    : bridge centrality (how often a case is on shortest paths)
  - local_pagerank        : community leadership (authority within its doctrine cluster)

Each signal is percentile-ranked (0-1) so scale differences don't bias the result.
The composite score is a weighted average of the three percentile ranks.

Default weights  (adjustable below):
  pagerank weight    = 0.40  - most proven signal for legal authority
  betweenness weight = 0.35  - bridge/gatekeeping role
  local_pr weight    = 0.25  - community leadership

Cases in the top 10% of all three signals independently are flagged as
SUPER_LANDMARK - these are the true indispensable cases.

Input:  full_graph/subcommunities/nodes.csv  (has all signals + subcommunity)

Output (full_graph/ranked/):
  ranked_cases.csv   - all cases sorted by composite score
  landmarks.csv      - cases in top 10% of all three signals
  community_top10.csv - top 10 cases per community by composite score
  stats.txt
"""

import os
import pandas as pd
import numpy as np

# ── config ────────────────────────────────────────────────────────────────────

IN_CSV  = os.path.join(os.path.dirname(__file__),
                        "full_graph", "subcommunities", "nodes.csv")
OUT_DIR = os.path.join(os.path.dirname(__file__),
                        "full_graph", "ranked")

W_PAGERANK    = 0.40
W_BETWEENNESS = 0.35
W_LOCAL_PR    = 0.25

# Top-N% threshold for super-landmark flag
LANDMARK_PERCENTILE = 0.90  # top 10%

# ── load ──────────────────────────────────────────────────────────────────────

print("Loading subcommunity node table ...")
df = pd.read_csv(IN_CSV)
print(f"  {len(df):,} nodes loaded")

# ── percentile ranks (0 = lowest, 1 = highest) ────────────────────────────────

def pct_rank(series: pd.Series) -> pd.Series:
    return series.rank(method="average", pct=True)

has_betweenness = "global_betweenness" in df.columns

df["pr_pct"] = pct_rank(df["global_pagerank"])

# local signal: prefer local_pagerank if present, else fall back to local_betweenness
if "local_pagerank" in df.columns:
    df["local_signal"] = df["local_pagerank"]
    local_signal_name  = "local_pagerank"
elif "local_betweenness" in df.columns:
    df["local_signal"] = df["local_betweenness"]
    local_signal_name  = "local_betweenness"
    print("  [INFO] using local_betweenness as local signal (local_pagerank not in dataset)")
else:
    df["local_signal"] = 0.0
    local_signal_name  = "none"
    print("  [WARN] no local signal found - local weight set to 0")

df["lpr_pct"] = pct_rank(df["local_signal"])

if has_betweenness:
    df["bw_pct"] = pct_rank(df["global_betweenness"])
    df["composite_score"] = (
        W_PAGERANK    * df["pr_pct"]
        + W_BETWEENNESS * df["bw_pct"]
        + W_LOCAL_PR    * df["lpr_pct"]
    )
else:
    print("  [WARN] global_betweenness not found - using 2-signal composite")
    w_pr  = W_PAGERANK  / (W_PAGERANK + W_LOCAL_PR)
    w_lpr = W_LOCAL_PR  / (W_PAGERANK + W_LOCAL_PR)
    df["bw_pct"] = np.nan
    df["composite_score"] = w_pr * df["pr_pct"] + w_lpr * df["lpr_pct"]

df["composite_score"] = df["composite_score"].round(6)

# ── landmark flag ─────────────────────────────────────────────────────────────

pr_thresh  = df["pr_pct"].quantile(LANDMARK_PERCENTILE)
lpr_thresh = df["lpr_pct"].quantile(LANDMARK_PERCENTILE)

if has_betweenness:
    bw_thresh = df["bw_pct"].quantile(LANDMARK_PERCENTILE)
    df["super_landmark"] = (
        (df["pr_pct"]  >= pr_thresh)
        & (df["bw_pct"]  >= bw_thresh)
        & (df["lpr_pct"] >= lpr_thresh)
    )
else:
    df["super_landmark"] = (
        (df["pr_pct"]  >= pr_thresh)
        & (df["lpr_pct"] >= lpr_thresh)
    )

n_landmarks = df["super_landmark"].sum()
print(f"  Super-landmarks (top {int((1-LANDMARK_PERCENTILE)*100)}% on all signals): {n_landmarks}")

# ── sort and select output columns ───────────────────────────────────────────

keep_cols = [
    "id", "label", "year", "url", "node_type",
    "community_id", "subcommunity_id",
    "composite_score", "super_landmark",
    "global_pagerank", "pr_pct",
    "local_pagerank", "local_betweenness", "lpr_pct",
    "global_betweenness", "bw_pct",
    "bridge_score",
    "in_degree", "out_degree",
    "community_size", "local_rank",
]
keep_cols = [c for c in keep_cols if c in df.columns]

ranked_df = df[keep_cols].sort_values("composite_score", ascending=False).reset_index(drop=True)
ranked_df.insert(0, "overall_rank", range(1, len(ranked_df) + 1))

# ── community top-10 ─────────────────────────────────────────────────────────

comm_top10 = (
    ranked_df.groupby("community_id", group_keys=False)
    .apply(lambda g: g.head(10))
    .reset_index(drop=True)
)

# ── export ────────────────────────────────────────────────────────────────────

os.makedirs(OUT_DIR, exist_ok=True)

ranked_csv = os.path.join(OUT_DIR, "ranked_cases.csv")
ranked_df.to_csv(ranked_csv, index=False)
print(f"\nWrote {ranked_csv}  ({len(ranked_df):,} rows)")

landmarks_csv = os.path.join(OUT_DIR, "landmarks.csv")
landmarks_df = ranked_df[ranked_df["super_landmark"]].copy()
landmarks_df.to_csv(landmarks_csv, index=False)
print(f"Wrote {landmarks_csv}  ({len(landmarks_df)} landmark cases)")

comm_csv = os.path.join(OUT_DIR, "community_top10.csv")
comm_top10.to_csv(comm_csv, index=False)
print(f"Wrote {comm_csv}  ({len(comm_top10)} rows across communities)")

# ── stats ─────────────────────────────────────────────────────────────────────

top30  = ranked_df.head(30)
lm_top = landmarks_df.sort_values("composite_score", ascending=False).head(30)

lines = [
    "Composite Case Ranking",
    "======================",
    f"Total ranked cases   : {len(ranked_df):,}",
    f"Super-landmarks      : {n_landmarks} (top {int((1-LANDMARK_PERCENTILE)*100)}% on all signals)",
    f"Weights              : PR={W_PAGERANK} | BW={W_BETWEENNESS} | LocalPR={W_LOCAL_PR}",
    "",
    "Top 30 by composite score:",
] + [
    f"  {row['overall_rank']:>4}. [score={row['composite_score']:.4f}] "
    f"[PR={row['global_pagerank']:.6f}] "
    f"[C{row['community_id']}] {'[LANDMARK] ' if row['super_landmark'] else ''}"
    f"{row['label'][:65]}"
    for _, row in top30.iterrows()
] + [
    "",
    "Super-landmarks (top 10% on ALL three signals):",
] + [
    f"  {row['overall_rank']:>4}. [score={row['composite_score']:.4f}] "
    f"[PR={row['global_pagerank']:.6f}] "
    f"[C{row['community_id']}] {row['label'][:65]}"
    for _, row in lm_top.iterrows()
]

stats_path = os.path.join(OUT_DIR, "stats.txt")
with open(stats_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"Wrote {stats_path}")

print()
print("\n".join(lines[:40]))
print("\nDone.")
