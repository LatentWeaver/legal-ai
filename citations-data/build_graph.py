"""
Build a directed citation graph from all JSON files in citations-data/.

Two input formats are handled:
  Format A (8 files): { case, year, url, precedents:[{case, url}] }
  Format B (1 file):  { source_name, source_doc_id, source_url,
                        precedents:[{name, doc_id, url}],
                        cited_by:[{name, doc_id, url}] }

Output (written to citations-data/full_graph/):
  graph.gexf   — directed graph for Gephi / community detection
  nodes.csv    — id, label, year, url, node_type
  edges.csv    — source, target
  stats.txt    — summary statistics
"""

import json
import os
import re
import networkx as nx
import pandas as pd

# Strip characters illegal in XML 1.0 (anything except tab/LF/CR in the C0 range)
_BAD_XML = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

def _clean(s: str) -> str:
    return _BAD_XML.sub('', s or '')

# ── config ────────────────────────────────────────────────────────────────────

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_FILES  = [
    ("751-1500",   "citations.json",          "A"),
    ("1501-2250",  "citations_standard.json", "A"),
    ("2251-3000",  "citation_standard.json",  "A"),
    ("3001-3750",  "citations_standard.json", "A"),
    ("3751-4500",  "citations.json",          "A"),
    ("4501-5250",  "citations_4501_5250.json","B"),
    ("5251-6000",  "citations.json",          "A"),
    ("6001-6750",  "citations.json",          "A"),
    ("6751-7500",  "citations.json",          "A"),
]
OUT_DIR = os.path.join(SCRIPT_DIR, "full_graph")

# ── helpers ───────────────────────────────────────────────────────────────────

_DOC_RE = re.compile(r"/doc/(\d+)/?$")

def doc_id_from_url(url: str) -> str | None:
    m = _DOC_RE.search(url or "")
    return m.group(1) if m else None


def load_format_a(path: str) -> list[dict]:
    """Return list of normalised records from Format-A JSON."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    records = []
    for entry in data:
        src_url = entry.get("url", "")
        src_id  = doc_id_from_url(src_url)
        if not src_id:
            continue
        records.append({
            "src_id":   src_id,
            "src_name": entry.get("case", ""),
            "src_year": entry.get("year", ""),
            "src_url":  src_url,
            "precedents": [
                {
                    "id":   doc_id_from_url(p.get("url", "")),
                    "name": p.get("case", ""),
                    "url":  p.get("url", ""),
                }
                for p in entry.get("precedents", [])
                if doc_id_from_url(p.get("url", ""))
            ],
            "cited_by": [],  # Format A has no cited_by data
        })
    return records


def load_format_b(path: str) -> list[dict]:
    """Return list of normalised records from Format-B JSON."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    records = []
    for entry in data:
        src_url = entry.get("source_url", "")
        src_id  = entry.get("source_doc_id") or doc_id_from_url(src_url)
        if not src_id:
            continue
        src_id = str(src_id)
        records.append({
            "src_id":   src_id,
            "src_name": entry.get("source_name", ""),
            "src_year": "",  # Format B doesn't carry year for the source
            "src_url":  src_url,
            "precedents": [
                {
                    "id":   str(p.get("doc_id") or doc_id_from_url(p.get("url", ""))),
                    "name": p.get("name", ""),
                    "url":  p.get("url", ""),
                }
                for p in entry.get("precedents", [])
                if p.get("doc_id") or doc_id_from_url(p.get("url", ""))
            ],
            "cited_by": [
                {
                    "id":   str(c.get("doc_id") or doc_id_from_url(c.get("url", ""))),
                    "name": c.get("name", ""),
                    "url":  c.get("url", ""),
                }
                for c in entry.get("cited_by", [])
                if c.get("doc_id") or doc_id_from_url(c.get("url", ""))
            ],
        })
    return records

# ── main ──────────────────────────────────────────────────────────────────────

def build_graph() -> nx.DiGraph:
    G = nx.DiGraph()

    # node_meta[doc_id] = {"label": ..., "year": ..., "url": ...}
    # We accumulate metadata separately so later files can fill in blanks.
    node_meta: dict[str, dict] = {}

    def ensure_node(node_id: str, name: str = "", year: str = "", url: str = "",
                    is_source: bool = False):
        name = _clean(name)
        if node_id not in node_meta:
            node_meta[node_id] = {"label": name, "year": year, "url": url,
                                   "is_source": is_source}
        else:
            meta = node_meta[node_id]
            if name and not meta["label"]:
                meta["label"] = name
            if year and not meta["year"]:
                meta["year"] = year
            if url and not meta["url"]:
                meta["url"] = url
            if is_source:
                meta["is_source"] = True

    all_records: list[dict] = []

    for folder, filename, fmt in DATA_FILES:
        path = os.path.join(SCRIPT_DIR, folder, filename)
        if not os.path.exists(path):
            print(f"  [WARN] missing: {path}")
            continue
        loader = load_format_a if fmt == "A" else load_format_b
        records = loader(path)
        all_records.extend(records)
        print(f"  loaded {len(records):>5} records from {folder}/{filename} (Format {fmt})")

    print(f"\nTotal records loaded: {len(all_records)}")

    for rec in all_records:
        src = rec["src_id"]
        ensure_node(src, rec["src_name"], rec["src_year"], rec["src_url"],
                    is_source=True)

        for p in rec["precedents"]:
            if not p["id"]:
                continue
            ensure_node(p["id"], p["name"], url=p["url"])
            G.add_edge(src, p["id"])

        for c in rec["cited_by"]:
            if not c["id"]:
                continue
            ensure_node(c["id"], c["name"], url=c["url"])
            G.add_edge(c["id"], src)  # cited_by[X] → src  means X cited src

    # Populate graph nodes with metadata
    for nid, meta in node_meta.items():
        node_type = "case" if meta["is_source"] else "reference"
        G.add_node(nid,
                   label=meta["label"],
                   year=meta["year"],
                   url=meta["url"],
                   node_type=node_type)

    return G, node_meta


def export(G: nx.DiGraph, node_meta: dict):
    os.makedirs(OUT_DIR, exist_ok=True)

    # GEXF
    gexf_path = os.path.join(OUT_DIR, "graph.gexf")
    nx.write_gexf(G, gexf_path)
    print(f"\nWrote {gexf_path}")

    # Nodes CSV
    rows = []
    for nid, data in G.nodes(data=True):
        rows.append({
            "id":        nid,
            "label":     data.get("label", ""),
            "year":      data.get("year", ""),
            "url":       data.get("url", ""),
            "node_type": data.get("node_type", "reference"),
        })
    nodes_df = pd.DataFrame(rows)
    nodes_csv = os.path.join(OUT_DIR, "nodes.csv")
    nodes_df.to_csv(nodes_csv, index=False)
    print(f"Wrote {nodes_csv}  ({len(nodes_df)} nodes)")

    # Edges CSV
    edges = [(u, v) for u, v in G.edges()]
    edges_df = pd.DataFrame(edges, columns=["source", "target"])
    edges_csv = os.path.join(OUT_DIR, "edges.csv")
    edges_df.to_csv(edges_csv, index=False)
    print(f"Wrote {edges_csv}  ({len(edges_df)} edges)")

    # Stats
    in_degree  = sorted(G.in_degree(), key=lambda x: x[1], reverse=True)
    out_degree = sorted(G.out_degree(), key=lambda x: x[1], reverse=True)

    def node_label(nid):
        return G.nodes[nid].get("label", nid)

    lines = [
        f"Total nodes : {G.number_of_nodes()}",
        f"Total edges : {G.number_of_edges()}",
        f"Source cases (have own entry): "
            f"{sum(1 for m in node_meta.values() if m['is_source'])}",
        f"Reference nodes (only cited)  : "
            f"{sum(1 for m in node_meta.values() if not m['is_source'])}",
        "",
        "Top 20 most-cited (highest in-degree):",
    ] + [
        f"  {rank+1:>3}. [{deg:>5}] {node_label(nid)[:80]}"
        for rank, (nid, deg) in enumerate(in_degree[:20])
    ] + [
        "",
        "Top 20 most-citing (highest out-degree):",
    ] + [
        f"  {rank+1:>3}. [{deg:>5}] {node_label(nid)[:80]}"
        for rank, (nid, deg) in enumerate(out_degree[:20])
    ]

    stats_path = os.path.join(OUT_DIR, "stats.txt")
    with open(stats_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {stats_path}")
    print()
    print("\n".join(lines[:6]))


if __name__ == "__main__":
    print("Building citation graph …\n")
    G, node_meta = build_graph()
    export(G, node_meta)
    print("\nDone.")
