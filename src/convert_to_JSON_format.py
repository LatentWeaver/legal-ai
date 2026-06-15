import json

input_path = "data/citations.jsonl"
output_path = "data/citations_formatted.json"

results = []
with open(input_path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        results.append({
            "case": entry["source_name"],
            "year": str(entry["source_year"]),
            "url": entry["source_url"],
            "precedents": [
                {"case": p["name"], "url": p["url"]}
                for p in entry.get("precedents", [])
            ],
        })

with open(output_path, "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print(f"Converted {len(results)} cases → {output_path}")
