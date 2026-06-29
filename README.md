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
```

## Co-occurrence Matrix Implementation

The co-occurrence matrix step is implemented in:

```text
scripts/build_cooccurrence_matrix.py
```

It reads informative terms from:

```text
codebooks/np_codebook.csv
codebooks/ne_codebook.csv
```

Then it scans case texts, splits each case into context windows, counts term pairs that appear in the same window, and writes both count and binary co-occurrence outputs.

Example with a folder of `.txt` case files:

```bash
python3 scripts/build_cooccurrence_matrix.py \
  --texts-dir data/case-texts \
  --window paragraph \
  --threshold 5 \
  --output-dir outputs/cooccurrence
```

Example with a CSV containing a `text` column:

```bash
python3 scripts/build_cooccurrence_matrix.py \
  --texts-csv data/cases.csv \
  --text-column text \
  --id-column case_id \
  --window paragraph \
  --threshold 5 \
  --output-dir outputs/cooccurrence
```

Supported context windows:

- `paragraph`
- `sentence`
- `document`
- `tokens`

For fixed token windows:

```bash
python3 scripts/build_cooccurrence_matrix.py \
  --texts-dir data/case-texts \
  --window tokens \
  --token-window-size 200 \
  --token-window-step 200 \
  --threshold 5
```

Outputs:

```text
outputs/cooccurrence/
├── cooccurrence_pairs.csv
├── cooccurrence_pairs_binary.csv
├── cooccurrence_counts.csv
├── cooccurrence_binary.csv
├── term_window_counts.csv
└── run_stats.txt
```

For very large codebooks, use `--skip-dense-matrices` to avoid writing the full term-by-term matrix and only produce pair lists.
