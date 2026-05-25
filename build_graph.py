#!/usr/bin/env python3
"""
build_graph.py
==============

Build a directed citation graph from the CSVs produced by extract_citations.py
and emit summary statistics, a human-readable report, and a degree-distribution
plot.

Inputs (from --outdir, default ./data):
  nodes.csv   id, case, year
  edges.csv   citing_id, cited_id   (intra-corpus edges)

Outputs (to --outdir):
  report.md         human-readable summary (also printed to stdout)
  degree_dist.png   log-scale in/out-degree distribution

The graph is built on the INTRA-CORPUS edge list (both endpoints are cases in
the corpus). All nodes from nodes.csv are added first, so cases with no
citations in either direction are correctly counted as isolated.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from typing import Optional

import networkx as nx

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt  # noqa: E402

LOG = logging.getLogger("build_graph")


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load_graph(nodes_path: str, edges_path: str) -> nx.DiGraph:
    """Load nodes and edges into a directed graph with case/year attributes."""
    g = nx.DiGraph()

    with open(nodes_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                nid = int(r["id"])
            except (KeyError, ValueError):
                continue
            g.add_node(nid, case=r.get("case", ""), year=r.get("year", ""))

    n_self = 0
    with open(edges_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                a, b = int(r["citing_id"]), int(r["cited_id"])
            except (KeyError, ValueError):
                continue
            if a == b:
                n_self += 1
                continue  # drop self-citations
            # endpoints should already be in nodes; add defensively if not
            if a not in g:
                g.add_node(a, case="", year="")
            if b not in g:
                g.add_node(b, case="", year="")
            g.add_edge(a, b)

    if n_self:
        LOG.info("Dropped %d self-citation edge(s)", n_self)
    return g


# --------------------------------------------------------------------------- #
# Reporting helpers
# --------------------------------------------------------------------------- #
def _label(g: nx.DiGraph, nid: int) -> str:
    case = g.nodes[nid].get("case", "") or "(unknown)"
    year = g.nodes[nid].get("year", "")
    case = case.replace("_", " ")
    if len(case) > 60:
        case = case[:57] + "..."
    return f"{case} ({year})" if year else case


def _top_table(g: nx.DiGraph, degree_view, title: str, k: int = 10) -> str:
    rows = sorted(degree_view, key=lambda kv: kv[1], reverse=True)[:k]
    lines = [f"### {title}", "", "| # | degree | case | id |", "|---|---|---|---|"]
    for i, (nid, deg) in enumerate(rows, 1):
        lines.append(f"| {i} | {deg} | {_label(g, nid)} | {nid} |")
    lines.append("")
    return "\n".join(lines)


def build_report(g: nx.DiGraph) -> str:
    n, m = g.number_of_nodes(), g.number_of_edges()
    density = nx.density(g)
    isolates = list(nx.isolates(g))
    wccs = list(nx.weakly_connected_components(g))
    largest = max((len(c) for c in wccs), default=0)

    parts: list[str] = []
    parts.append("# Citation Graph Report\n")
    parts.append("## Summary\n")
    parts.append(f"- Nodes (cases): **{n}**")
    parts.append(f"- Edges (citations): **{m}**")
    parts.append(f"- Density: **{density:.6f}**")
    parts.append(f"- Isolated nodes (no citations either way): **{len(isolates)}** "
                 f"({100 * len(isolates) / n:.1f}% of nodes)" if n else "- Isolated nodes: 0")
    parts.append(f"- Weakly connected components: **{len(wccs)}**")
    parts.append(f"- Largest component: **{largest}** nodes "
                 f"({100 * largest / n:.1f}% of nodes)" if n else "- Largest component: 0")
    parts.append("")
    parts.append(_top_table(g, g.in_degree(), "Top 10 most-cited cases (in-degree)"))
    parts.append(_top_table(g, g.out_degree(), "Top 10 most-citing cases (out-degree)"))
    return "\n".join(parts)


def save_degree_plot(g: nx.DiGraph, path: str) -> None:
    in_deg = [d for _, d in g.in_degree()]
    out_deg = [d for _, d in g.out_degree()]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for ax, data, title in ((axes[0], in_deg, "In-degree"), (axes[1], out_deg, "Out-degree")):
        if data and max(data) > 0:
            ax.hist(data, bins=min(50, max(data) + 1), log=True)
        ax.set_title(f"{title} distribution")
        ax.set_xlabel("degree")
        ax.set_ylabel("count (log)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run(args: argparse.Namespace) -> int:
    nodes_path = os.path.join(args.outdir, "nodes.csv")
    edges_path = os.path.join(args.outdir, "edges.csv")
    for p in (nodes_path, edges_path):
        if not os.path.exists(p):
            LOG.error("Missing input: %s (run extract_citations.py first)", p)
            return 2

    g = load_graph(nodes_path, edges_path)
    LOG.info("Loaded graph: %d nodes, %d edges", g.number_of_nodes(), g.number_of_edges())

    report = build_report(g)
    report_path = os.path.join(args.outdir, "report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")

    plot_path = os.path.join(args.outdir, "degree_dist.png")
    save_degree_plot(g, plot_path)

    print("\n" + report + "\n")
    LOG.info("Wrote %s and %s", report_path, plot_path)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build a citation graph and report from nodes/edges CSVs.")
    p.add_argument("--outdir", default="data", help="directory holding nodes.csv/edges.csv and receiving outputs")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
