# Additional U.S. Legal Data Resources

This note summarizes the additional U.S. sources we researched for the
medical malpractice / personal injury pipeline, how we would extract from them,
and where each source is most useful.

## Recommended Role In The Pipeline

| Source | Role |
| --- | --- |
| CourtListener | Primary case-law source for opinions, metadata, and citation graph |
| CAP (Harvard Caselaw Access Project) | Secondary overlap source for validation, deduplication, and occasional backfill |
| RECAP / PACER via CourtListener | Enrichment source for federal dockets, filings, facts, and procedure |
| FJC Integrated Database (IDB) | Metadata enrichment for federal cases |
| OpenJurist | Backup/validation source for older federal opinions |
| Justia | Manual validation/discovery source |
| Casetext / CoCounsel | Not a practical public extraction path for this project |

## Source Summary

| Source | What is present | How to extract | Helpful for this project | What needs more work |
| --- | --- | --- | --- | --- |
| [CourtListener Bulk Data](https://wiki.free.law/c/courtlistener/help/api/bulk-data/bulk-legal-data) | Courts, dockets, opinion clusters, opinions, citations map, parentheticals, embeddings; bulk release notes note `filepath_pdf_harvard` in opinion clusters | Bulk CSV snapshots from the Free Law Project S3 bucket; REST API for object inspection; local filtering after bulk download | Best primary source for building case nodes and citation edges; already fits the current med-mal pipeline; strongest source for downstream graph/community work | Inspect raw bulk schema in more detail, especially the exact citation-related fields and `filepath_pdf_harvard` for CAP linkage |
| [CAP / Harvard Caselaw Access Project](https://case.law/) and [static.case.law](https://static.case.law/us/1/CasesMetadata.json) | Reporter/volume-based case metadata and case text files; fields include `id`, `name`, `decision_date`, `citations`, `court`, `jurisdiction`, `cites_to`, `analysis`, `provenance`, `file_name` | Bulk-by-reporter / volume download from `static.case.law`; no topic filter, so download broader volumes and filter locally | Useful for validation, deduplication, and occasional backfill; strong because it carries reporter citation strings directly | Improve CAP -> CourtListener dedup using citation strings first; quantify overlap more broadly than the current bounded pilot |
| [RECAP coverage](https://www.courtlistener.com/help/coverage/recap/) and [CourtListener PACER/RECAP APIs](https://wiki.free.law/c/courtlistener/help/api/rest/v4/pacer-data) | Federal dockets, docket entries, RECAP documents, parties, attorneys, filing metadata, extracted filing text | CourtListener REST API with authentication token; some workflows also require PACER credentials/billing | Good for facts/evidence/procedure enrichment; useful if the project later wants complaints, motions, orders, and procedural timelines | Confirm which fields are public vs token-gated, and whether a small repeatable extraction should be added to the repo beyond `recap_fetch_demo.py` |
| [FJC Integrated Database (IDB)](https://www.fjc.gov/research/idb) | Federal case metadata for district courts, appeals, bankruptcy, and criminal/civil records (1970-present) | Official FJC downloads/documentation; can also appear merged into CourtListener when requested by Free Law Project | Good metadata enrichment layer for federal PI / med-mal cases; can improve structured attributes without replacing opinion text sources | Map FJC fields into the team schema and decide whether to use FJC directly or only via CourtListener-enriched records |
| [OpenJurist](https://openjurist.org/) | Browsable federal case-law archive organized by reporter/volume/page, with full opinion pages | Likely scrape/page-based extraction only; no public bulk/API path found during research | Useful backup/validation source, especially for older federal opinions and reporter-based lookups | Check scraping feasibility, licensing/usage comfort, and whether it adds real coverage beyond CourtListener/CAP |
| [Justia Case Law](https://law.justia.com/cases/) and [Justia Dockets](https://dockets.justia.com/) | Broad browsable federal/state case-law pages and a separate dockets/filings site | Public browsing/search; no public bulk/API path found during research | Useful for manual spot-checking, discovering case categories, and validating whether cases appear elsewhere | Confirm whether any non-public partner/licensed feed exists; otherwise keep as validation-only |
| [Casetext -> Thomson Reuters CoCounsel](https://casetext.com/) | Commercial legal research / AI product surface rather than an open corpus path | No public bulk/API path found during research | Not a practical extraction source for this project | No immediate action unless the team has separate institutional access |

## Similar Project Signals

These are not necessarily new extraction sources, but they are useful signals
for what similar legal-AI work actually builds on.

| Project / Dataset | Why it matters here |
| --- | --- |
| [Pile of Law](https://huggingface.co/datasets/pile-of-law/pile-of-law) | Includes `courtListener_opinions` and `courtListener_docket_entry_documents`, which is a strong signal that CourtListener/RECAP are standard open legal-AI building blocks |
| [Pile of Law paper](https://arxiv.org/abs/2207.00220) | Confirms the broader research pattern of aggregating many legal text sources into a training corpus rather than relying on a single portal |
| [CaseHOLD paper](https://arxiv.org/abs/2104.08671) | Shows that large-scale legal NLP work often starts from very large U.S. decision corpora and focuses on downstream reasoning tasks |
| [VerbCL paper](https://arxiv.org/abs/2108.10120) | Especially relevant because it derives citation/quotation relations from CourtListener, aligning with this repo's graph-oriented goals |

## Current Practical Takeaways

- CourtListener should remain the primary U.S. source.
- CAP should mostly be treated as a validation + deduplication source, not a
  separate main corpus.
- RECAP is valuable if we want factual/procedural filing text, but it does not
  replace opinion/citation data.
- FJC IDB is the most promising enrichment source to research next if federal
  metadata becomes important.
- OpenJurist and Justia are best kept in the backup/validation bucket unless a
  real coverage gap appears.
- Casetext is not currently useful as a public data-collection path.

## Best Next Research / Implementation Steps

1. Inspect CourtListener bulk fields more closely, especially CAP-linkage and
   citation-related fields.
2. Strengthen CAP vs CourtListener dedup using citation strings as the first
   match key.
3. Decide whether RECAP should stay a research note or become a small optional
   enrichment pipeline.
4. If federal metadata becomes important, map FJC IDB fields into the shared
   U.S. schema.
