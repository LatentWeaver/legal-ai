# PageRank Community Detection — Results

## Approach

Two-stage pipeline applied to the merged Indian land dispute citation graph (~6,700 source cases, 1950–2025):

1. **Louvain community detection** on the *undirected* citation graph — partitions nodes into macro-clusters by maximising intra-cluster edge density (modularity). Captures topical groupings without being biased by citation direction.

2. **PageRank** on the *directed* graph, computed at two scopes:
   - **Global PageRank** — authority of a case across the entire network. A high score means the case is cited by many other highly-cited cases.
   - **Local (within-community) PageRank** — authority of a case relative to its Louvain community peers. Answers: *"who is the landmark precedent inside this cluster?"*

### Why this over pure Louvain or pure PageRank

| Method | What it gives | What it misses |
|---|---|---|
| Louvain only | Community structure | No authority ranking inside communities |
| PageRank only | Global authority ranking | No community/topic grouping |
| **Louvain + PageRank** | Communities **and** ranked authority within each | — |

---

## Graph Statistics

| Metric | Value |
|---|---|
| Total nodes | 124,083 |
| Total edges | 224,171 |
| Source case nodes | 6,351 |
| Reference-only nodes (statutes, external cases) | 117,732 |
| Louvain communities | **172** |
| Largest community | 10,922 nodes (C0) |
| Smallest community | 2 nodes |

---

## Top 20 Nodes by Global PageRank

Statutes and constitutional articles naturally dominate — every case cites the CPC or Article 226 — which is expected and meaningful.

| Rank | PageRank | Community | Node |
|------|----------|-----------|------|
| 1 | 0.002096 | C2 | the Code of Civil Procedure |
| 2 | 0.001198 | C63 | M.S. Narayana Menon @ Mani vs State Of Kerala & Anr |
| 3 | 0.001096 | C34 | Union of India vs Pramod Gupta (2005) |
| 4 | 0.001083 | C123 | T. Lakshmipathi & Ors vs P. Nithyananda Reddy & Ors |
| 5 | 0.001021 | C90 | The Oriental Insurance Company Ltd vs Meena Variyal & Ors |
| 6 | 0.001013 | C5 | Article 226 |
| 7 | 0.001002 | C90 | M/S. National Insurance Co. Ltd vs Baljit Kaur And Ors |
| 8 | 0.000969 | C4 | State of Haryana vs Ch. Bhajan Lal (J.T.) |
| 9 | 0.000932 | C94 | Jindal Stainless Ltd. & Anr. vs State of Haryana & Ors |
| 10 | 0.000916 | C — | Kailash vs State Of M.P. |
| 11 | 0.000907 | C — | Ghurey Lal vs State Of U.P. (2008) |
| 12 | 0.000892 | C — | J.K. Industries Ltd. vs Union of India (2007) |
| 13 | 0.000861 | C90 | National Insurance Co. Ltd vs Laxmi Narain Dhut |
| 14 | 0.000852 | C — | State (N.C.T. of Delhi) vs Navjot Sandhu @ Afsan Guru |
| 15 | 0.000843 | C — | State of Punjab vs Devans Modern Breweries Ltd (2003) |
| 16 | 0.000840 | C — | Gorige Pentaiah vs State Of A.P. & Ors |
| 17 | 0.000829 | C — | State of West Bengal vs Kesoram Industries Ltd (2004) |
| 18 | 0.000821 | C — | State of H.P. vs Gujarat Ambuja Cement Ltd (2005) |
| 19 | 0.000815 | C — | Pradeep Kumar Biswas vs Indian Institute of Chemical Biology (2002) |
| 20 | 0.000810 | C — | Popat And Kotecha Property vs State Bank of India Staff Association |

---

## Top 20 Communities by Size

| Community | Size | Case nodes | Top authority node (global PR) |
|-----------|------|-----------|-------------------------------|
| C0 | 10,922 | 1,063 | Maharashtra State Board of Secondary & Higher Secondary Education vs Paritosh Bhupesh Kurmarsheth |
| C2 | 7,913 | 1,097 | the Code of Civil Procedure |
| C4 | 7,289 | 988 | State of Haryana vs Ch. Bhajan Lal (J.T.) |
| C33 | 4,259 | 41 | State (N.C.T. of Delhi) vs Navjot Sandhu @ Afsan Guru |
| C5 | 4,132 | 597 | Article 226 |
| C90 | 3,613 | 77 | New India Assurance Co. vs Satpal Singh & Ors |
| C125 | 2,850 | 18 | Gorige Pentaiah vs State Of A.P. & Ors |
| C43 | 2,815 | 45 | Bondar Singh & Ors. vs Nihal Singh & Ors. |
| C129 | 2,745 | 38 | G.M. Tank vs State Of Gujarat & Anr |
| C11 | 2,614 | 194 | Sridevi & Ors vs Jayaraja Shetty & Ors |
| C94 | 2,411 | 30 | Jindal Stainless Ltd. vs State of Haryana & Ors |
| C82 | 2,359 | 27 | Delhi Development Authority vs M/S. R.S. Sharma & Co. |
| C136 | 2,343 | 25 | — |
| C15 | 2,284 | 231 | — |
| C89 | 2,259 | 26 | — |
| C8 | 2,251 | 321 | B.R. Mehta vs Smt. Atma Devi & Ors (1987) |
| C121 | 2,117 | 25 | — |
| C148 | 2,102 | 29 | — |
| C107 | 2,093 | 31 | — |
| C61 | 2,045 | 24 | — |

### Notable community themes (from top authority nodes)

| Community | Inferred theme |
|-----------|---------------|
| C0 | Education law / constitutional rights in education |
| C2 | Civil procedure (CPC) — the broadest cross-cutting cluster |
| C4 | Criminal procedure / FIR quashing (*Bhajan Lal* doctrine) |
| C5 | Writ jurisdiction / Article 226 constitutional petitions |
| C8 | Property transfer / land succession |
| C90 | Motor accident / insurance compensation |
| C94 | Tax / entry tax / constitutional validity of state levies |
| C11 | Land property disputes / title suits |
| C43 | Land acquisition / adverse possession |

---

## Output Files

| File | Description |
|------|-------------|
| `citations-data/full_graph/louvain_pagerank/nodes.csv` | 124K rows — `id, label, year, url, node_type, community_id, global_pagerank, local_pagerank, local_rank, in_degree, out_degree, community_size` |
| `citations-data/full_graph/louvain_pagerank/community_summary.csv` | 172 communities — size, top authority node, top-5 members |
| `citations-data/full_graph/louvain_pagerank/graph.gexf` | Annotated graph for Gephi (includes community_id, global_pagerank, local_pagerank) |
| `citations-data/full_graph/louvain_pagerank/stats.txt` | Full ranked lists |
| `citations-data/full_graph/pagerank/pagerank_nodes.csv` | Pure PPR community run (30 seed communities) |

## Scripts

| Script | Purpose |
|--------|---------|
| `citations-data/build_graph.py` | Merges all 9 JSON files → unified directed graph |
| `citations-data/pagerank_communities.py` | Personalized PageRank communities (30 seeds) |
| `citations-data/louvain_pagerank.py` | Louvain macro-communities + per-community PageRank |
