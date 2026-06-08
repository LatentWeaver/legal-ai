"""Build a case citation graph from scraped citations.

Reads data/citations.jsonl (produced by scrape_citations.py) and the cases CSV,
then builds a DIRECTED graph where an edge  source_case -> cited_precedent
means "source cites precedent".

It writes two graphs:

  * full     - every cited doc_id is a node, even ones outside our corpus.
  * induced  - only cases that are in our land-dispute corpus (cases.csv), i.e.
               edges where BOTH endpoints are land-dispute cases. This is the
               graph we run community detection on, because it describes the
               internal precedent structure of land-dispute law.

Node attributes (case name, year) are attached from cases.csv where known.

USAGE
-----
    python src/build_graph.py
    python src/build_graph.py --citations data/citations.jsonl --outdir data/graph
"""

import argparse
import json
import os
import re

import networkx as nx
import pandas as pd

DOC_ID_RE = re.compile(r"/doc(?:fragment)?/(\d+)")


def extract_doc_id(url: str):
    m = DOC_ID_RE.search(url or "")
    return m.group(1) if m else None


def load_corpus(cases_csv: str) -> dict[str, dict]:
    """doc_id -> {case, year} for every case in the corpus."""
    df = pd.read_csv(cases_csv)
    df["doc_id"] = df["link"].map(extract_doc_id)
    df = df[df["doc_id"].notna()]
    meta = {}
    for _, r in df.iterrows():
        meta[str(r["doc_id"])] = {
            "case": r.get("case"),
            "year": int(r["year"]) if pd.notna(r.get("year")) else None,
        }
    return meta


def build(citations_jsonl: str, corpus: dict) -> nx.DiGraph:
    """Directed multigraph collapsed to weighted DiGraph (weight = #times cited)."""
    G = nx.DiGraph()
    n_records = n_edges = 0
    with open(citations_jsonl, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            src = str(rec["source_doc_id"])
            G.add_node(src)
            for p in rec.get("precedents", []):
                dst = str(p["doc_id"])
                # attach a name for out-of-corpus nodes from the dropdown text
                if dst not in G or "name" not in G.nodes[dst]:
                    G.add_node(dst, name=p.get("name"))
                if G.has_edge(src, dst):
                    G[src][dst]["weight"] += 1
                else:
                    G.add_edge(src, dst, weight=1)
                n_edges += 1
            n_records += 1
    # annotate corpus membership + metadata
    for node in G.nodes():
        meta = corpus.get(node)
        G.nodes[node]["in_corpus"] = meta is not None
        if meta:
            G.nodes[node]["case"] = meta["case"]
            G.nodes[node]["year"] = meta["year"]
    print(f"Read {n_records} source cases, {n_edges} citation links.")
    return G


def induced_subgraph(G: nx.DiGraph, corpus: dict) -> nx.DiGraph:
    keep = [n for n in G.nodes() if n in corpus]
    return G.subgraph(keep).copy()


def write_graph(G: nx.DiGraph, outdir: str, prefix: str):
    os.makedirs(outdir, exist_ok=True)
    # GEXF can't store None; coerce attributes to safe values
    H = G.copy()
    for _, d in H.nodes(data=True):
        for k, v in list(d.items()):
            if v is None:
                d[k] = ""
    gexf = os.path.join(outdir, f"{prefix}.gexf")
    nx.write_gexf(H, gexf)
    edges = pd.DataFrame(
        [(u, v, d.get("weight", 1)) for u, v, d in G.edges(data=True)],
        columns=["source", "target", "weight"],
    )
    edges.to_csv(os.path.join(outdir, f"{prefix}_edges.csv"), index=False)
    nodes = pd.DataFrame(
        [{"node_id": n, **d} for n, d in G.nodes(data=True)]
    )
    nodes.to_csv(os.path.join(outdir, f"{prefix}_nodes.csv"), index=False)
    print(f"  wrote {gexf}  ({G.number_of_nodes()} nodes, {G.number_of_edges()} edges)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--citations", default="data/citations.jsonl")
    ap.add_argument("--cases", default="data/cases.csv")
    ap.add_argument("--outdir", default="data/graph")
    args = ap.parse_args()

    corpus = load_corpus(args.cases)
    print(f"Corpus size (doc_ids in cases.csv): {len(corpus)}")

    G = build(args.citations, corpus)
    print("\nFull graph:")
    write_graph(G, args.outdir, "citation_full")

    Gi = induced_subgraph(G, corpus)
    in_corpus_nodes = sum(1 for n in G if G.nodes[n].get("in_corpus"))
    print(f"\nInduced (corpus-only) graph: {in_corpus_nodes} corpus cases scraped")
    write_graph(Gi, args.outdir, "citation_induced")

    # quick health stats on the induced graph
    if Gi.number_of_nodes():
        ud = Gi.to_undirected()
        comp = max(nx.connected_components(ud), key=len) if ud.number_of_edges() else set()
        print(f"\nInduced graph stats:")
        print(f"  isolated nodes (no in-corpus citation): "
              f"{sum(1 for n in Gi if Gi.degree(n) == 0)}")
        print(f"  largest connected component: {len(comp)} nodes")


if __name__ == "__main__":
    main()
