# legal-ai: Land Dispute Case Graph
## Overview

This repository is intended to support a legal AI research pipeline over Indian case law, focused initially on land dispute cases from Indian Kanoon. The target corpus is approximately 26k cases from 1950–2025.

## Current Repository Reference Material

The repository currently contains these reference areas:

```text
legal-ai/
├── README.md
├── codebook-reference/
│   ├── Rake.ipynb
│   ├── Spacy_NE.ipynb
│   ├── Spacy_NP.ipynb
│   ├── Textacy.ipynb
│   ├── Yake.ipynb
│   └── discount_frequency.py
├── community-detection-reference/
│   └── community_detection.ipynb
└── scaling-ancohits/
    ├── ANCOHITS.m
    ├── Anco_HIT_Algorithm.py
    ├── NARRA-SCALE_Scaling_Users_and_Messaging_Through_Narrative_Detection_in_Retweet_Networks.pdf
    └── Partisan Scale.pdf
```

These are only for reference implementations. The next step is to refactor them into reusable Python modules and scripts that operate on legal case data instead of the original example datasets.

## Research Objective

Build a pipeline that can:

1. Collect and normalize Indian Kanoon case metadata and text for land dispute cases.

2. Construct a case citation graph, where cases are nodes and citations are directed edges.

3. Detect legal communities and subcommunities using Louvain.

4. Rank important cases within each community using PageRank, HITS, in-degree, out-degree, and betweenness centrality.

5. Generate a legal codebook using noun chunks and named entities, then manually label the top 3,000 terms as informative or non-informative.

6. For a given context window, identify non-independent co-occurring bigrams, n-grams, noun chunks, and named entities using QUIC-Scaling.

7. Build “molecules” or template patterns, by connecting co-occurring informative terms according to their legal roles. A proposed molecule structure includes issue, evidence, rule, actors, and procedural metadata, connected by typed edges.

8. Use molecule patterns to partition subsets of cases into legally meaningful groups.

9. Prepare a co-clustering matrix of molecule patterns to identify which features tend to occur together and may be dependent.

10. Map molecule features onto a bipartite graph connecting cases and legal features. Add signed edges based on plaintiff/defendant win-loss outcomes.

11. Run ANCO-HITS on the signed case-feature graph to identify winning and losing legal patterns, preferably issue by issue.

12. Test whether a new case can be grouped with similar precedent cases and legal themes using citation communities, molecule patterns, and ANCO-HITS rankings.


## Data Source

Target source:

- Indian Kanoon
- Years: 1950–2025
- Size: 26k cases
- Domain focus: land disputes
- Domain focus size: 7.5k cases
- Location: https://drive.google.com/drive/folders/1_omgPYIvnrn0WAd9yzkOA-m6iA4EnNX0?usp=drive_link

---

## Contributor branch: Wei-An Wang — Master Citations Dataset & Scraper

> Full detail: [`master-citations-dataset/README.md`](master-citations-dataset/README.md)

Implements **step 1** (collect & normalize case text/metadata) and **step 2** (citation graph) of the pipeline above for the land/property bracket **#6001–6750**, and supplies the per-case **outcome + signed citation sentiment** used by **steps 10–11**.

**Scraper** ([`master-citations-dataset/scraper/`](master-citations-dataset/scraper/)) — a Cloudflare-resilient Indian Kanoon scraper: Playwright over **real Chrome** (CDP, trusted events), retry/backoff + circuit breaker + coordinate-calibrated Turnstile click, two modes (`master` = full content + bidirectional citations; `content` = related-case full content), resume-safe SQLite (WAL). Each case is parsed into metadata, **8 semantic paragraph types**, citation **sentiment** (Pos/Neg/Party/Neutral), and full text/HTML.

**Dataset** — **750 master cases** (judgment years 2015–2021) with the **complete bidirectional citation network**: **99,771 edges** (25,012 _Cites_ + 74,759 _Cited-by_).
- Citation network JSON (17 MB, 750 records): [`master-citations-dataset/data/master_citations_6001_6750.json`](master-citations-dataset/data/master_citations_6001_6750.json)
- Full SQLite DB (218 MB → 62 MB gz) on Google Drive: [⬇ download](https://drive.google.com/file/d/10n1jUQ9dj6etGACz4eEcWvlhyeqRMXh_/view?usp=sharing)

**Current progress (2026-06-08):** Phase 1 (750/750 masters — content + both citation directions) complete & verified. Phase 2 (full content of all ~72k cited/citing cases) in progress — **~39.8k/72k (~55%)**; the ~7.8 GB related-case output stays off-repo but is fully reproducible from the JSON + scraper.
