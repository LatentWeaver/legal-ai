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
2. Construct a case citation graph, where cases are nodes and citations are edges.
3. Detect legal communities and subcommunities using louvain.
4. Rank important cases inside each community using centrality algorithms such as PageRank, HITS, in-degree, out-degree, and betweenness.
5. Generate a legal codebook using noun chunks, named entities, and frequent non-independent n-grams.
6. Manually annotate terms into useful legal categories such as evidence, issue, rule, procedural metadata, and non-informative phrases.
7. Build a legal knowledge graph by connecting non independently co-occuring bigrams/ngrams/entities from codebook.
8. Run ANCOHITS scoring over cases and claims/themes with argument as edges
9. Test whether a new case can be grouped or sorted with similar precedent and legal themes.

## Data Source

Target source:

- Indian Kanoon
- Years: 1950–2025
- Approximate target size: 26k cases
- Domain focus: land disputes
- Location: https://drive.google.com/drive/folders/1_omgPYIvnrn0WAd9yzkOA-m6iA4EnNX0?usp=drive_link
```
