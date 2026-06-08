# Citation pipeline

Turns the land-dispute case list into a citation graph with detected
communities. Four steps:

```
data/cases.csv ──► scrape_citations_api.py ──► data/citations.jsonl
                                                  │
                          ┌───────────────────────┼───────────────────────┐
                          ▼                                                ▼
                  export_citations_csv.py                            build_graph.py
                          │                                                │
                          ▼                                                ▼
                  data/citations.csv (readable)            data/graph/*.gexf
                                                                           │
                                                                           ▼
                                                              run_louvain.py
                                                                           │
                                                                           ▼
                                                       data/graph/communities.csv
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Step 1 — scrape citations (Indian Kanoon API, headless)

Indian Kanoon is behind Cloudflare, so plain HTTP scraping is blocked. We use
the official token-based API (`api.indiankanoon.org`) instead — no browser, no
captcha. Get a token at https://api.indiankanoon.org/ and set it:

```bash
export IK_API_TOKEN=your_token_here
```

Then:

```bash
# smoke test: first 10 cases, caching raw API responses
python src/scrape_citations_api.py --limit 10 --save-raw

# a row range (1-based, inclusive) — use to split work across people
python src/scrape_citations_api.py --start 1 --end 750

# one year, or everything (resumable — safe to stop/restart)
python src/scrape_citations_api.py --year 1990
python src/scrape_citations_api.py
```

Progress is saved to `data/citations.jsonl` after every case; re-running skips
cases already done. `--save-raw` caches each full API response under
`data/raw_docs/<id>.json` so the judgment text can be reused later (and you
don't pay the API twice).

## Step 2 — export a readable CSV (optional)

```bash
python src/export_citations_csv.py
```

Writes `data/citations.csv`: one row per citation
(`source_case, source_year, cited_doc_id, cited_name, ...`) for easy review in
a spreadsheet.

## Step 3 — build the citation graph

```bash
python src/build_graph.py
```

Writes to `data/graph/`:
- `citation_full.*` — every cited doc, including ones outside our corpus
  (statutes, non-corpus cases).
- `citation_induced.*` — only land-dispute cases (edges where both endpoints
  are in `cases.csv`). **This is the graph for community detection.**

## Step 4 — Louvain communities + centrality

```bash
python src/run_louvain.py                 # on the induced graph
python src/run_louvain.py --betweenness   # also compute betweenness (slower)
```

Writes `data/graph/communities.csv`: each case with its community id and
PageRank / HITS / in-degree / out-degree / betweenness, so the leading
precedent in each community can be ranked.

## Notes

- `data/raw_docs/` and the duplicate `data/cases.csv` are git-ignored; the
  citation outputs (`citations.jsonl`, `citations.csv`, `graph/`) are committed.
- IK's "cites" include statute provisions (Act/section links), not just case
  precedents, so `citations.jsonl` counts are higher than case-only counts.
  Statutes are not in our corpus, so they drop out of the **induced** case graph
  automatically — community detection is unaffected. Separating statutes from
  cases in the full graph is a deferred refinement.
- Community structure becomes meaningful at full scale (all ~7,500 cases),
  because citations point backward in time — a single early row-range is mainly
  a correctness check of scraping + the pipeline.
```
