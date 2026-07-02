import requests

import json

import csv

import time

import os

TOKEN = os.environ.get("COURTLISTENER_TOKEN")

url = "https://www.courtlistener.com/api/rest/v4/search/"

headers = {"Authorization": f"Token {TOKEN}"}

params = {

    "q": "\"medical malpractice\" \"standard of care\"",

    "type": "o",

    "order_by": "score desc"

}

# Words that signal a case is NOT a real malpractice merits case

EXCLUDE_KEYWORDS = ["insurance", "in re", "joint underwriting"]

collected = []

next_url = url

request_count = 0

MAX_REQUESTS = 15  # safety cap, well under the 125/day limit

while next_url and len(collected) < 100 and request_count < MAX_REQUESTS:

    if next_url == url:

        response = requests.get(next_url, headers=headers, params=params)

    else:

        response = requests.get(next_url, headers=headers)

    request_count += 1

    data = response.json()

    for case in data.get("results", []):

        name_lower = case.get("caseName", "").lower()

        if any(bad in name_lower for bad in EXCLUDE_KEYWORDS):

            continue

        collected.append(case)

        if len(collected) >= 100:

            break

    next_url = data.get("next")

    time.sleep(1)  # be polite to the API

print(f"Collected {len(collected)} clean medical malpractice cases using {request_count} requests.\n")

# Save full case data as JSON (mirrors citations.json style)

with open("medical-malpractice-data/citations.json", "w") as f:

    json.dump(collected, f, indent=2)

# Save a simplified nodes.csv (mirrors nodes.csv style: id, case, year)

with open("medical-malpractice-data/nodes.csv", "w", newline="") as f:

    writer = csv.writer(f)

    writer.writerow(["id", "case", "year"])

    for case in collected:

        case_id = case.get("cluster_id")

        case_name = case.get("caseName", "").replace(",", "")

        date_filed = case.get("dateFiled") or ""

        year = date_filed[:4] if date_filed else ""

        writer.writerow([case_id, case_name, year])

print("Saved citations.json and nodes.csv to medical-malpractice-data/")
