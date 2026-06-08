# Master Citations Dataset & Scraper — Wei-An Wang

Indian Kanoon land/property dispute cases **#6001–6750** of `land_property_dispute_cases.csv`, scraped to full judgment depth, together with the **complete bidirectional citation network** for each case — and the Cloudflare-resilient scraper that produced them.

This contribution feeds the repository's pipeline at **step 1** (collect & normalize case text/metadata), **step 2** (citation graph: cases = nodes, citations = directed edges), and supplies the per-case **outcome + signed citation sentiment** needed for **steps 10–11** (signed case–feature bipartite graph → ANCO-HITS).

---

## TL;DR

- **750 master cases** — bracket 6001–6750 (land/property disputes, judgment years **2015–2021**).
- **99,771 citation edges** — **25,012 _Cites_** (precedents each master relies on) + **74,759 _Cited-by_** (later cases that cite the master). 746/750 masters have ≥1 citation; on average ~33 precedents and ~100 citing cases per master.
- Each master parsed into **structured metadata + 8 semantic paragraph types + citation sentiment (Pos/Neg/Party/Neutral) + full text & HTML**.
- The **citation network** ships here as JSON: [`data/master_citations_6001_6750.json`](data/master_citations_6001_6750.json) (17 MB, 750 records).
- The **full SQLite database** (218 MB — metadata, semantic text, sentiment, and raw judgment HTML for all 750) is hosted on Google Drive (see [Full database](#full-database-google-drive)).

---

## Contents

```text
master-citations-dataset/
├── README.md
├── data/
│   └── master_citations_6001_6750.json   # 750 records — bidirectional citation network
└── scraper/
    ├── scrape_master_citations.py         # runner: "master" mode + "content" mode
    ├── scraper.py                         # core BrowserScraper + parse_case_html()
    ├── cloudflare_bypass.py               # Cloudflare challenge handling
    ├── calibrate_cf.py                    # one-time Turnstile coordinate calibration
    ├── config.py                          # scrape configuration
    └── requirements.txt                   # Python dependencies
```

---

## The dataset

### Scope

The 750 cases are the **1-indexed rows 6001–6750** of `land_property_dispute_cases.csv` (the ~7.5k land/property subset of the repo's target corpus). Judgment-year distribution:

| Year | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 |
|------|------|------|------|------|------|------|------|
| Cases | 59 | 130 | 129 | 117 | 150 | 162 | 3 |

### Citation network (this JSON)

`data/master_citations_6001_6750.json` is a JSON array of 750 records. Each record follows the team's reference citation format (the per-case precedent list) and **extends it with the inbound `cited_by` direction**:

```jsonc
{
  "source_name": "Smt. <Party> vs State Of Maharashtra",
  "source_url": "https://indiankanoon.org/doc/<tid>/",
  "precedent_count": 31,                      // cases THIS master cites ("Cites")
  "precedents": [
    { "name": "<Cited case name>", "doc_id": "1766147", "url": "https://indiankanoon.org/doc/1766147/" }
    // ...
  ],
  "cited_by_count": 88,                        // later cases that cite THIS master ("Cited by")
  "cited_by": [
    { "name": "<Citing case name>", "doc_id": "104928668", "url": "https://indiankanoon.org/doc/104928668/" }
    // ...
  ]
}
```

Both lists are taken from the **complete** clickable `[Cites N, Cited by M]` search endpoints (`/search/?formInput=cites:<tid>` and `citedby:<tid>`), not the partial inline-citation subset — so the counts match Indian Kanoon's header. Statute/section pages are filtered out of the related-case work-list (but remain visible as named entries here).

### Per-case content (full SQLite DB)

Beyond the citation lists, every master was parsed by `parse_case_html()` into a rich row. The `cases` table columns, by group:

- **Identity / metadata** — `tid`, `title`, `publishdate`, `judge`, `bench`, `case_number`, `citation`, `jurisdiction`, `petitioner`, `respondent`, `docsource`, `outcome`
- **Counts** — `num_statutes_cited`, `cites_count`, `cited_by_count`
- **Semantic paragraph counts (8)** — `semantic_Facts`, `semantic_Issue`, `semantic_PetArg`, `semantic_RespArg`, `semantic_Section`, `semantic_Precedent`, `semantic_CDiscource`, `semantic_Conclusion`
- **Citation sentiment counts (4)** — `cite_pos`, `cite_neg`, `cite_party`, `cite_neutral`
- **Semantic text (8)** — `text_Facts`, `text_Issues`, `text_Petitioners_Arguments`, `text_Respondents_Arguments`, `text_Analysis_of_the_law`, `text_Precedent_Analysis`, `text_Courts_Reasoning`, `text_Conclusion`
- **Full document** — `doc_text` (plain text, ~88 KB avg), `doc_html` (raw HTML, ~112 KB avg)

Companion tables: `citation_search(case_tid, direction, ref_tid, ref_name, ref_date, ref_court)` — the raw edge list; `outbound_citations` — inline in-text citations **with per-citation sentiment**; `inbound_citations`; `search_status` — resume bookkeeping.

**Citation sentiment** is derived from Indian Kanoon's `data-sentiment` markup on each citation span and is what makes the signed graph possible:

| Label | Meaning | Pipeline use |
|-------|---------|--------------|
| `Pos` | Precedent **accepted / relied on** by the court | + edge (winning pattern) |
| `Neg` | Precedent **distinguished / negatively viewed** | − edge (losing pattern) |
| `PARTY` | Precedent **cited by a party**, not (yet) endorsed by the court | neutral/contextual |
| `Neutral` | Mentioned without explicit stance | neutral |

### Full database (Google Drive)

The full DB exceeds GitHub's 100 MB limit, so it is hosted on Drive (gzip-compressed to 62 MB):

- **Download:** [master_citations_cases.db.gz (62 MB) — Google Drive](https://drive.google.com/file/d/10n1jUQ9dj6etGACz4eEcWvlhyeqRMXh_/view?usp=sharing)
- **File:** `master_citations_cases.db.gz` (62 MB compressed → 218 MB SQLite)
- **Restore:** `gunzip master_citations_cases.db.gz` → open with any SQLite client.
- **Quick peek:**
  ```bash
  sqlite3 master_citations_cases.db "SELECT count(*) FROM cases;"            # 750
  sqlite3 master_citations_cases.db "SELECT direction, count(*) FROM citation_search GROUP BY direction;"
  ```

---

## The scraper

A general Indian Kanoon scraper hardened against Cloudflare, driving a **real Chrome** over the DevTools protocol so that all interactions are genuine (`isTrusted`) events.

### Architecture
- **Playwright `connect_over_cdp`** to a user-launched Chrome on port 9222 (real profile, real fingerprint).
- **`parse_case_html()`** extracts the metadata, 8 semantic paragraph types (from Indian Kanoon's `data-structure` attributes), citation sentiment, and full text/HTML described above.
- **SQLite (WAL mode)** for resume-safe, concurrent-read storage.

### Two modes
- **`--mode master`** (default) — for each master case: scrape the `/doc/` page to full depth, then follow **both** the `Cites` and `Cited by` search endpoints to capture the complete citation lists. Writes the rich `cases` rows + `citation_search` edges and exports the JSON above.
- **`--mode content`** — take the citation `ref_tid`s as a work-list (statutes filtered out) and scrape **those** related cases to full content (no recursive re-citation). Used to expand the network's nodes to full text.

### Cloudflare resilience
- `_goto_with_retry()` — 4 attempts with 15/30/45/60 s backoff on `net::ERR_ABORTED` / timeouts / 52x.
- **Circuit breaker** — after 3 consecutive blocked cases, pause for a manual pass in the visible browser, then resume.
- **Coordinate-calibrated Turnstile click** (`calibrate_cf.py`) — human-like click at a pre-measured widget position when a challenge appears.
- Tunable pacing: `--delay` (between requests) and `--nav-wait` (post-navigation settle).

### Reproduce this dataset
```bash
pip install -r scraper/requirements.txt
python -m playwright install chromium    # or use system Chrome via CDP

# launch Chrome with a debug port (separate profile):
open -na "Google Chrome" --args --remote-debugging-port=9222 \
  --user-data-dir="$HOME/chrome-debug" --no-first-run --no-default-browser-check

# scrape masters 6001–6750 (content + both citation directions):
python scraper/scrape_master_citations.py --mode master --start 6001 --end 6750 \
  --delay 3.0 --nav-wait 3.0

# (optional) expand to full content of all cited / citing cases:
python scraper/scrape_master_citations.py --mode content --direction both
```

---

## Current progress (2026-06-08)

- **Phase 1 — complete & verified:** 750/750 masters scraped to full content with both citation directions (0 name mismatches vs the CSV). This is what ships here (JSON) and on Drive (full DB).
- **Phase 2 — in progress:** scraping the **full content of every cited / citing case** (~72k unique related cases after statute filtering). As of 2026-06-08: **~39.8k / 72k (~55%)**. 100% of sampled related cases trace back to a master's citation list. That output (`related_cases_cases.db`, ~7.8 GB) is **not** uploaded due to size — but every related case's `doc_id`/`url` is already in the JSON here, so the node set is fully reproducible from the scraper.
