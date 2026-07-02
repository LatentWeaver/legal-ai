# CAP (Caselaw Access Project) Research Notes

## Access
- CAP API and search tool sunset September 5, 2024
- Data now bulk-download only at static.case.law
- Organized by reporter/volume, no topic filter
- Download full volumes and filter locally by keyword

## Date Range
- Coverage confirmed from 1754
- Cutoff approximately 2020

## Schema
Each case record contains:
- id: CAP internal ID (NOT a CourtListener ID)
- name: case name
- decision_date: date of decision
- citations: citation strings e.g. "1 U.S. 1"
- court: court name and abbreviation
- jurisdiction: state/territory
- cites_to: list of cited cases
- analysis: OCR confidence, word count, sha256
- provenance: source Harvard, batch year

## Dedup Strategy vs CourtListener
- CAP records have NO CourtListener URL or cross-reference ID
- Source-ID/URL dedup will NOT work directly between the two systems
- Best approach: citation string matching e.g. "1 U.S. 1" appears in both
- Fallback: case name + decision year
- CourtListener has already imported 1M+ items from CAP so overlap is heavy

## Conclusion
CAP not worth extracting as separate primary source given heavy CourtListener overlap.
Best used for validation and dedup spot-checks only.
