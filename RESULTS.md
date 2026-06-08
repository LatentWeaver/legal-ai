# Pilot results — citations for cases 1–750

Citations scraped via the Indian Kanoon API for the first 750 land-dispute
cases in `scraper-reference/land_property_dispute_cases.csv` (years 1950–1962).

## Data files

| File | What it is |
|---|---|
| `data/citations.jsonl` | One JSON object per source case: its precedents (cited doc_ids, names, URLs). |
| `data/citations.csv` | Same data flattened to one row per citation (case name + cited name + links) for easy review. |
| `data/graph/citation_induced.*` | Citation graph among corpus cases only (edges where both cases are land-dispute cases) — used for community detection. |
| `data/graph/citation_full.*` | Full graph including all cited docs (statutes + non-corpus cases). |
| `data/graph/communities.csv` | Each case with its Louvain community + PageRank/HITS/degree/betweenness. |

## Numbers

- **750** cases scraped, **13,079** citation links, avg **17.4** cites/case
  (only 2.3% had zero). Extracted counts match IK's own `numcites` exactly.
- Induced (case-to-case) graph: **753 nodes, 599 edges**; largest connected
  component 269 nodes.
- Louvain: **432 communities**, modularity **0.68** (inflated by isolated
  early cases that cite mostly pre-1950 / statute authorities outside the
  corpus).

## Notes / caveats

- These are the **earliest** cases, so the induced graph is sparse — most of
  their citations point to pre-1950 cases or statutes outside the corpus.
  Density increases substantially once later row-ranges (which cite back into
  these landmarks) are added. The pilot validates the pipeline end-to-end.
- The largest communities' top precedents by PageRank are genuine land/property
  landmarks (e.g. *State of Bihar v. Kameshwar Singh* — Bihar Land Reforms;
  *Visweshwar Rao* — MP zamindari; *Chiranjit Lal Chowdhuri*), confirming the
  method recovers coherent legal themes.
- IK "cites" include statute/section links, not just case precedents; these
  drop out of the induced case graph automatically. Separating them in the full
  graph is a deferred refinement.

## Reproduce

See `src/README.md`. In short: set `IK_API_TOKEN`, then
`scrape_citations_api.py` → `export_citations_csv.py` → `build_graph.py` →
`run_louvain.py`.
