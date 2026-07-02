# CAP Schema Mapping for U.S. Case Data

## Purpose

This note maps Harvard CAP (Caselaw Access Project) records to the U.S. case
schema currently used in this repo. It also captures the recommended
deduplication strategy against CourtListener.

## Current Team Decision

- CourtListener remains the primary U.S. source.
- CAP should be treated as a secondary source for validation and dedup spot
  checks, not as a separate primary corpus.

Why:

- CAP API/search was sunset on September 5, 2024.
- CAP is now bulk-download only.
- CAP has heavy overlap with CourtListener.
- CAP has no direct CourtListener URL or cross-reference ID.

## Current U.S. Output Schemas in This Repo

### 1. Canonical case JSON

File example:

- `citations-data-us-medical-malpractice/1-750/citations_formatted.json`

Shape:

```json
{
  "case": "United States v. Williams",
  "year": "1836",
  "url": "https://www.courtlistener.com/opinion/8347406/united-states-v-williams/",
  "precedents": [
    {
      "case": "Slee v. Bloom",
      "url": "https://www.courtlistener.com/opinion/5629373/slee-v-bloom/"
    }
  ]
}
```

Required keys:

- `case`
- `year`
- `url`
- `precedents`

### 2. Rich JSONL case records

File example:

- `citations-data-us-medical-malpractice/1-750/citations.jsonl`

Shape:

```json
{
  "source": "courtlistener",
  "source_cluster_id": "8347406",
  "source_opinion_ids": ["8316016"],
  "source_case": "United States v. Williams",
  "source_year": 1836,
  "source_url": "https://www.courtlistener.com/opinion/8347406/united-states-v-williams/",
  "source_name": "United States v. John Williams",
  "precedent_count": 6,
  "precedents": [
    {
      "name": "Slee v. Bloom",
      "cluster_id": "5629373",
      "opinion_ids": ["5474493"],
      "citing_opinion_ids": ["8316016"],
      "url": "https://www.courtlistener.com/opinion/5629373/slee-v-bloom/"
    }
  ]
}
```

### 3. Flat citation edge CSV

File example:

- `citations-data-us-medical-malpractice/1-750/citations.csv`

Columns:

- `source`
- `source_cluster_id`
- `source_opinion_ids`
- `source_case`
- `source_year`
- `source_url`
- `cited_cluster_id`
- `citing_opinion_ids`
- `cited_opinion_ids`
- `cited_name`
- `cited_url`

## CAP Record Shape

Based on the current CAP research notes, each CAP case record includes:

- `id`
- `name`
- `decision_date`
- `citations`
- `court`
- `jurisdiction`
- `cites_to`
- `analysis`
- `provenance`

Important caveat:

- CAP `id` is a CAP-only ID.
- CAP records do not carry CourtListener URLs or cluster IDs.

## CAP -> Canonical Schema Mapping

| CAP field | Meaning | Canonical field | Mapping notes |
|---|---|---|---|
| `name` | Case name | `case` | Direct mapping |
| `decision_date` | Decision date | `year` | Use first 4 digits if valid |
| none | Public case URL | `url` | No direct CourtListener URL available; CAP-only URL would not satisfy current validator if CourtListener-only linkage is expected |
| `cites_to` | Cases cited by this case | `precedents` | Map each cited case into `{case, url}` when possible |

### Canonical mapping constraints

The current validator expects:

- a non-empty `case`
- a 4-digit `year`
- an `https://` URL
- each precedent to also have `case` and `https://` `url`

This means CAP cannot be dropped directly into the current canonical format
unless we first decide one of these:

1. allow CAP-native URLs in the canonical schema, or
2. leave CAP out of canonical exports and keep it only for validation/dedup.

Recommendation:

- Do not force CAP into the main canonical export right now.
- Use CAP records in a sidecar validation workflow instead.

## CAP -> Rich JSONL Mapping

| CAP field | Rich JSONL field | Mapping notes |
|---|---|---|
| fixed value `cap` | `source` | Source label should be `cap` |
| `id` | `source_cluster_id` | Not a true CourtListener cluster; keep as CAP-native source ID if needed |
| none | `source_opinion_ids` | Usually unavailable in CourtListener terms |
| `name` | `source_case` | Direct mapping |
| `decision_date` | `source_year` | Parse year |
| CAP case URL if available | `source_url` | Keep CAP-native URL, not CourtListener |
| `name` | `source_name` | Same as case name unless CAP exposes a fuller display name |
| len(`cites_to`) | `precedent_count` | Direct derived field |
| `cites_to` | `precedents` | Each cited case becomes a precedent object |

Suggested CAP precedent object shape:

```json
{
  "name": "Cited Case Name",
  "citation_string": "1 U.S. 1",
  "decision_year": 1754,
  "url": "https://..."
}
```

Note:

- This does not match the current CourtListener-rich precedent shape exactly,
  because CAP lacks cluster IDs and CourtListener opinion IDs.

## CAP -> Flat Edge CSV Mapping

CAP can still support a simpler edge table:

| Edge field | CAP source |
|---|---|
| `source` | fixed `cap` |
| `source_cluster_id` | CAP `id` |
| `source_case` | CAP `name` |
| `source_year` | parsed from `decision_date` |
| `source_url` | CAP case URL if available |
| `cited_name` | cited case name from `cites_to` |
| `cited_url` | cited case URL if available |

Fields that will usually be missing compared with CourtListener:

- `source_opinion_ids`
- `cited_cluster_id`
- `citing_opinion_ids`
- `cited_opinion_ids`

So CAP edge rows should be treated as lower-fidelity than CourtListener rows.

## Deduplication Strategy: CAP vs CourtListener

Direct dedup by source ID or source URL will not work because CAP does not
expose CourtListener IDs/URLs.

### Recommended matching order

1. `citation string`
   - Best primary key when the same reporter citation appears in both systems.
   - Example: `1 U.S. 1`
2. normalized `case name + decision year`
   - Fallback when citation strings are absent or inconsistent.
3. optional metadata checks
   - court
   - jurisdiction
   - decision date proximity

### Practical dedup recommendation

- Treat CourtListener as the source of truth.
- Use CAP only to check:
  - whether CourtListener missed a case,
  - whether a cited case name/date looks inconsistent,
  - whether sampled citations line up.

## Recommendation

### Use CAP for

- validation
- dedup spot checks
- schema comparison
- gap analysis on a small sample

### Do not use CAP for

- main U.S. case ingestion
- main citation graph construction
- primary med-mal or personal injury corpus generation

## Immediate Next Step

If the team wants a concrete follow-up, the next useful task is:

- take a small CAP sample
- normalize it into a CAP-specific staging format
- test dedup against a small CourtListener sample using:
  - citation string
  - fallback case name + year
