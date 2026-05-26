# Legal-AI: Citation Network Analysis of Indian Supreme Court Land & Property Dispute Cases (1950-2024)

## Overview

This project builds and analyzes a **directed citation graph** from 7,496 Indian Supreme Court land and property dispute judgments spanning 1950 to 2024. Case metadata and judgment links are sourced from [Indian Kanoon](https://indiankanoon.org), and citations between cases are extracted via web scraping to construct a network that reveals how judicial precedent flows through decades of property law.

## Findings Summary

### Dataset
- **7,496 unique cases** across 75 years (1950-2024), sourced from Indian Kanoon
- Cases distributed fairly evenly across decades, peaking in the 2010s (1,254 cases)
- Every case has a unique Indian Kanoon doc ID and a corresponding judgment URL

### Citation Graph (500-case run)
- **758 intra-corpus citation edges** extracted from 500 seed cases
- **9,258 total raw out-citations** (including references to cases outside the corpus)
- **Largest connected component: 366 nodes** (4.9% of graph) — a substantial, cohesive subnetwork from only 6.7% of the corpus
- Graph density ~0.000013, consistent with legal citation networks where landmark cases accumulate citations while most cases cite only a few predecessors

### Most-Cited Cases (in-degree)
| Rank | Case | Year | In-degree |
|---|---|---|---|
| 1 | State of Bihar v. Maharajadhiraja Sir Kameshwar Singh | 1952 | 16 |
| 2 | Chiranjit Lal Chowdhuri v. Union of India | 1950 | 15 |
| 3 | A.K. Gopalan v. State of Madras | 1950 | 12 |
| 4 | Bengal Immunity Co. Ltd. v. State of Bihar | 1954 | 11 |
| 5 | Ram Singh v. State of Delhi | 1951 | 10 |
| 6 | State of Bombay v. F.N. Balsara | 1951 | 10 |
| 7 | Visweshwar Rao v. State of Madhya Pradesh | 1952 | 10 |

### Most-Citing Cases (out-degree)
| Rank | Case | Year | Out-degree |
|---|---|---|---|
| 1 | Kesavananda Bharati v. State of Kerala | 1973 | 12 |
| 2 | ADM Jabalpur v. S.S. Shukla | 1976 | 10 |
| 3 | Babulal Amthalal Mehta v. Collector of Customs, Calcutta | 1957 | 9 |
| 4 | I.C. Golak Nath v. State of Punjab | 1967 | 7 |
| 5 | Maneka Gandhi v. Union of India | 1978 | 6 |
| 6 | Jindal Stainless Ltd. v. State of Haryana | 2016 | 6 |

### Key Observations
1. **Power-law degree distribution**: Both in-degree and out-degree follow heavy-tailed distributions on a log scale. A handful of landmark cases attract the vast majority of citations, while most cases have degree 0-1.
2. **Foundational 1950s cases dominate in-degree**: The top citation targets are from 1950-1954, the earliest years of the Supreme Court. These cases established constitutional precedent on property rights (Art. 19, 31), due process (Art. 21, 22), and zamindari abolition that later cases built upon.
3. **Landmark "synthesizer" cases dominate out-degree**: The top citing cases are well-known constitutional law landmarks (*Kesavananda Bharati*, *ADM Jabalpur*, *Golak Nath*, *Maneka Gandhi*) that surveyed and consolidated decades of prior precedent. At 500 cases, *Kesavananda Bharati* emerges as the most prolific citer with 12 intra-corpus references.
4. **Temporal citation asymmetry**: Citations flow strictly forward in time (later cases cite earlier ones), producing a DAG structure consistent with legal stare decisis.
5. **Large connected component**: The largest weakly connected component spans 366 nodes (4.9% of the full graph), demonstrating that even a 6.7% sample of the corpus produces a substantial, interconnected citation network.
6. **New entrants at scale**: Scaling from 100 to 500 cases revealed cases like *Chiranjit Lal Chowdhuri* (in-degree 15) and *Bengal Immunity Co.* (in-degree 11) that were invisible at smaller sample sizes, and *Jindal Stainless* (2016, out-degree 6) showing modern cases that heavily cite historical precedent.

### Degree Distribution

![Degree Distribution](data/degree_dist.png)

### Scaling Estimate
At the observed rate (~1.52 intra-corpus edges per case), a full 7,496-case run would yield an estimated **11,400+ intra-corpus edges**, producing a dense citation network suitable for community detection, PageRank-based importance scoring, and temporal analysis of how property law doctrine evolves.

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
