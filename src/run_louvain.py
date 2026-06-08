"""Run Louvain community detection + centrality on the citation graph.

Mirrors the reference pipeline in community-detection-reference/
community_detection.ipynb, adapted from a retweet network to a legal citation
network.

Louvain needs an undirected graph, so we collapse the directed citation graph
to undirected (edge weight = number of citations in either direction). For each
case we then compute the centrality measures the README asks for (PageRank,
HITS, in/out-degree, betweenness) so the most important precedent inside each
community can be ranked.

USAGE
-----
    python src/run_louvain.py
    python src/run_louvain.py --graph data/graph/citation_induced.gexf \
        --resolution 1.0 --out data/graph/communities.csv
"""

import argparse

import networkx as nx
import pandas as pd
from community import community_louvain


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", default="data/graph/citation_induced.gexf")
    ap.add_argument("--resolution", type=float, default=1.0)
    ap.add_argument("--out", default="data/graph/communities.csv")
    ap.add_argument("--betweenness", action="store_true",
                    help="also compute betweenness (slow on large graphs)")
    args = ap.parse_args()

    DG = nx.read_gexf(args.graph)
    print(f"Loaded {DG.number_of_nodes()} nodes, {DG.number_of_edges()} directed edges.")

    # collapse to undirected weighted graph for Louvain
    UG = nx.Graph()
    for u, v, d in DG.edges(data=True):
        w = d.get("weight", 1)
        if UG.has_edge(u, v):
            UG[u][v]["weight"] += w
        else:
            UG.add_edge(u, v, weight=w)
    UG.add_nodes_from(DG.nodes())
    print(f"Undirected graph for Louvain: {UG.number_of_nodes()} nodes, "
          f"{UG.number_of_edges()} edges.")

    partition = community_louvain.best_partition(
        UG, weight="weight", resolution=args.resolution, random_state=42
    )
    n_comm = len(set(partition.values()))
    modularity = community_louvain.modularity(partition, UG, weight="weight")
    print(f"Found {n_comm} communities. Modularity = {modularity:.4f}")

    # centrality measures (on the directed graph where direction matters)
    print("Computing centralities...")
    pagerank = nx.pagerank(DG, weight="weight") if DG.number_of_edges() else {}
    try:
        hits_hub, hits_auth = nx.hits(DG, max_iter=500)
    except Exception:
        hits_hub, hits_auth = {}, {}
    in_deg = dict(DG.in_degree())
    out_deg = dict(DG.out_degree())
    betw = (nx.betweenness_centrality(DG, weight="weight")
            if args.betweenness and DG.number_of_edges() else {})

    rows = []
    for node in DG.nodes():
        attr = DG.nodes[node]
        rows.append({
            "node_id": node,
            "case": attr.get("case", ""),
            "year": attr.get("year", ""),
            "name": attr.get("name", ""),
            "community": partition.get(node),
            "pagerank": pagerank.get(node, 0.0),
            "hits_authority": hits_auth.get(node, 0.0),
            "hits_hub": hits_hub.get(node, 0.0),
            "in_degree": in_deg.get(node, 0),
            "out_degree": out_deg.get(node, 0),
            "betweenness": betw.get(node, 0.0),
        })
    df = pd.DataFrame(rows).sort_values(
        ["community", "pagerank"], ascending=[True, False]
    )
    df.to_csv(args.out, index=False)
    print(f"Wrote {args.out}")

    # show the top precedent per community by PageRank
    sizes = df.groupby("community")["node_id"].count().sort_values(ascending=False)
    print("\nLargest communities (top precedent by PageRank):")
    for comm in sizes.head(10).index:
        sub = df[df["community"] == comm]
        top = sub.iloc[0]
        label = top["case"] or top["name"] or top["node_id"]
        print(f"  community {comm}: {sizes[comm]} cases | top: {label}")


if __name__ == "__main__":
    main()
