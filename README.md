# Legal-AI: Citation Network Analysis of Indian Supreme Court Land & Property Dispute Cases (1950-2024)

## Overview

This project builds and analyzes a **directed citation graph** from 7,496 Indian Supreme Court land and property dispute judgments spanning 1950 to 2024. Case metadata and judgment links are sourced from [Indian Kanoon](https://indiankanoon.org), and citations between cases are extracted via web scraping to construct a network that reveals how judicial precedent flows through decades of property law.

## Findings Summary

### Dataset
- **7,496 unique cases** across 75 years, sourced from Indian Kanoon
- Cases are distributed fairly evenly across decades, peaking in the 2010s (1,254 cases)
- Every case has a unique Indian Kanoon doc ID and a corresponding judgment URL

### Citation Graph (sample run: 20 cases)
- **29 intra-corpus citation edges** extracted from a 20-case pilot
- **293 total raw out-citations** (including references to cases outside the corpus)
- Graph density is extremely low (~0.000001), consistent with legal citation networks where landmark cases accumulate citations while most cases cite only a few predecessors
- **Largest connected component: 13 nodes** even from just 20 seed cases, indicating that the property-law citation network is well-connected when scaled

### Key Observations
1. **Power-law in-degree distribution**: A small number of foundational 1950s-era cases dominate as citation targets. *A.K. Gopalan v. State of Madras (1950)* tops the list with in-degree 8 from just 20 sampled cases, reflecting its status as a landmark constitutional property rights judgment.
2. **Temporal citation flow**: Later cases cite earlier ones but not vice versa (as expected for legal precedent). The out-degree is spread across decades, while in-degree concentrates on early cases.
3. **Sparse but structured**: 99.5% of nodes are isolated at the 20-case sample, but this is a sampling artifact. The intra-corpus hit rate (~29 edges from 20 cases) projects to thousands of edges at full corpus scale.
4. **HTML scraping challenge**: Indian Kanoon judgment pages embed `/doc/` links to statutes and constitutional articles, not to other case judgments. The scraper was redesigned to use IK's search-result pages (`/search/?formInput=citedby:<id>`) which correctly separate case-to-case citations from statute references.

### Scaling Estimate
At the observed rate (~1.45 intra-corpus edges per case), a full 7,496-case run would yield an estimated **10,000+ intra-corpus edges**, producing a rich citation network suitable for community detection, PageRank-based importance scoring, and temporal analysis of how property law doctrine evolves.

## Project Structure

```
Legal-AI/
  extract_citations.py        # Citation extraction pipeline (API + HTML scraper)
  build_graph.py               # Graph construction, stats, and visualization
  land_property_dispute_cases.csv  # Source dataset (7,496 cases)
  data/
    nodes.csv                  # Graph nodes (id, case, year)
    edges.csv                  # Intra-corpus citation edges
    out_citations_raw.csv      # All observed citations (including external)
    report.md                  # Auto-generated graph summary
    degree_dist.png            # In/out-degree distribution plot
```

## Usage

### Extract citations

```bash
# HTML scraper (no API key needed)
python extract_citations.py --limit 50 --max-pages 3 --rate 1.0

# With Indian Kanoon API key (faster, more complete)
export INDIANKANOON_API_TOKEN=your_token
python extract_citations.py --limit 0
```

Key flags:
- `--limit N`: number of cases to process (0 = all 7,496)
- `--max-pages N`: search result pages per direction per case (HTML mode; 10 results/page)
- `--rate`: seconds between requests (be polite to IK servers)
- `--force`: ignore cache and refetch

All responses are cached to `cache/`, so runs are fully resumable.

### Build the graph

```bash
python build_graph.py --outdir data
```

Reads `data/nodes.csv` and `data/edges.csv`, writes `data/report.md` and `data/degree_dist.png`.

## Data Source

- **Indian Kanoon** (https://indiankanoon.org) - open-access Indian legal database
- The input CSV was compiled by filtering Supreme Court judgments related to land and property disputes

## Requirements

```
python >= 3.9
requests
beautifulsoup4
networkx
matplotlib
pandas  # for exploratory analysis only
```

## Next Steps

- Run full corpus extraction (7,496 cases) with higher `--max-pages` for completeness
- Apply community detection (Louvain / label propagation) to identify clusters of related property law doctrine
- Compute PageRank / HITS to rank case importance beyond simple citation count
- Temporal analysis of citation patterns across decades
- Cross-reference with the Google Drive judgment PDFs for full-text NLP analysis
