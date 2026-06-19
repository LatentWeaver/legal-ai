#!/usr/bin/env python3
import json, os

RAW = "/scratch/kpate385/legalft/IndicLegalQA Dataset_10K_Revised.json"
OUTDIR = "/scratch/kpate385/legalft/data"

# Flip to True to keep only property/land-dispute pairs (simple keyword match).
PROPERTY_ONLY = False
PROP_KEYWORDS = ["property","land","tenant","tenancy","landlord","eviction",
                 "possession","easement","partition","lease","mortgage",
                 "encroach","occupancy","house","premises","rent","title deed"]

os.makedirs(OUTDIR, exist_ok=True)
with open(RAW, encoding="utf-8") as f:
    raw = json.load(f)

def is_property(rec):
    blob = (rec.get("question","")+" "+rec.get("answer","")+" "+rec.get("case_name","")).lower()
    return any(k in blob for k in PROP_KEYWORDS)

rows = []
for rec in raw:
    q = (rec.get("question") or "").strip()
    a = (rec.get("answer") or "").strip()
    if not q or not a:
        continue
    if PROPERTY_ONLY and not is_property(rec):
        continue
    rows.append({"instruction": q, "input": "", "output": a})

with open(os.path.join(OUTDIR,"legal_sft.json"),"w",encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=2)

with open(os.path.join(OUTDIR,"dataset_info.json"),"w",encoding="utf-8") as f:
    json.dump({"legal_sft":{"file_name":"legal_sft.json",
        "columns":{"prompt":"instruction","query":"input","response":"output"}}},
        f, ensure_ascii=False, indent=2)

print(f"wrote {len(rows)} examples to {OUTDIR}/legal_sft.json  (property_only={PROPERTY_ONLY})")
