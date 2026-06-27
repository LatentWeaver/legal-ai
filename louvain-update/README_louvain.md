# Community & Sub-Community Detection (Louvain)

Two-level Louvain community detection on the Indian Kanoon citation graph. Detects top-level **communities** and
then **sub-communities** within the larger ones, on both the **directed** and
**undirected** citation graph.

Prototyped on the 4501–5250 master-case bracket; the same script runs on the
merged corpus with no code change (just point `--json` at the combined file).

---

## What it does

For each case in the input JSON, edges are built from the citation data
(`A → B` means case A cites case B). The script then:

1. **Builds the citation graph** two ways:
   - **Directed** (`DiGraph`) — preserves citation direction.
   - **Undirected** — treats a citation as a symmetric link.
2. **Runs Louvain** for the top-level communities on each graph.
   NetworkX's `louvain_communities` applies the **directed modularity gain
   (Dugué & Perez)** when given a `DiGraph`, and the standard formulation when
   undirected — so the two partitions genuinely differ.
3. **Selects the significantly-larger communities** (those above
   `mean + 1·std` of community sizes, or a fixed `--top-k`) and runs Louvain
   **again within each** to produce sub-communities.
4. **Writes** per-case assignments and a comparison summary.

This follows the two-level pattern used in the lab's NARRA-SCALE work
(communities → sub-communities).

---

## Usage

```bash
pip install networkx
python louvain_v3.py
```

The script expects, in the same folder:
- `citations_4501_5250.json` — the citation data (or any file in the same schema)
- `land_property_dispute_cases.csv` — used to label nodes with readable case names

Options:

```bash
python louvain_v3.py --select topk --top-k 5   # sub-cluster the 5 largest instead of mean+std
python louvain_v3.py --json merged.json        # run on the merged corpus
```

---

## Outputs

| File | Contents |
|------|----------|
| `communities_directed.csv` | per-case `community`, `subcommunity`, in/out citations (directed run) |
| `communities_undirected.csv` | same, for the undirected run |
| `louvain_v3_summary.txt` | sizes, selection rationale, and directed-vs-undirected comparison |

---

## Input schema

Each record in the JSON:

```json
{
  "source_doc_id": "832890",
  "source_name": "...",
  "precedents":  [{ "name": "...", "doc_id": "1676100", "url": "..." }],
  "cited_by":    [{ "name": "...", "doc_id": "126591761", "url": "..." }]
}
```

Communities are keyed on `doc_id`. Case names are pulled from the CSV by
`doc_id` (the scraped `source_name` is unreliable for this batch, so the CSV
is the source of truth for names).

---

## Results on the 4501–5250 bracket (prototype)

- 750 cases, 564 in-bracket citation edges
- ~53 communities on each graph, modularity ≈ 0.81
- Directed and undirected give **different partitions** (different community
  sizes/membership), but at single-bracket scale the difference is small
  (modularity 0.809 vs 0.809). Direction is expected to matter more on the
  merged graph, where citation chains are longer.

---

## Caveats (read before interpreting)

These are real limitations, not formalities:

- **Single-bracket artifact.** Many cases cite cases *outside* this 750-case
  slice, so within the bracket they appear isolated (~300 nodes). This inflates
  modularity and isolation counts. Both should change substantially on the
  merged corpus. Treat all numbers here as prototype-scale, not final.
- **Duplicates.** A few cases appear under multiple Indian Kanoon doc IDs
  (e.g. "UOI vs Pramod Gupta" vs "Union Of India vs Pramod Gupta"). They land
  in the same community as expected but slightly distort counts. Running a
  dedup pass first gives cleaner results.
- **Run-to-run variation.** Louvain is non-deterministic; even with a fixed
  seed, exact community membership and sizes can shift slightly between runs
  and across NetworkX versions. The overall structure (≈53 communities,
  ~0.81 modularity) is stable; specific assignments are not guaranteed
  identical run-to-run. For robust claims about a specific cluster, a
  consensus/stability pass over multiple seeds would be the next step.
- **Community labels are not verified.** Any topical interpretation of a
  community (e.g. "these are taxation cases") is a hypothesis from case names,
  not a validated result.

---

## Next steps

- Run on the merged ~7,500-case corpus once citations are standardized.
- Decide directed vs undirected with the team based on the precedent-finding goal.
- Optionally: dedup pass before detection; stability/consensus testing across seeds.
