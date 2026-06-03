import re
import os
import requests
import pandas as pd
from dotenv import load_dotenv
from bs4 import BeautifulSoup

load_dotenv()
TOKEN = os.getenv("token")

BASE_URL = "https://api.indiankanoon.org"
HEADERS = {
    "Authorization": f"Token {TOKEN}",
    "Accept": "application/json",
}


def extract_doc_id(link: str) -> str | None:
    m = re.search(r"/doc/(\d+)/", link)
    return m.group(1) if m else None


def fetch_doc(doc_id: str) -> dict:
    resp = requests.post(
        f"{BASE_URL}/doc/{doc_id}/",
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def parse_citations(doc_html: str) -> list[dict]:
    """Extract unique cited doc IDs and titles from the HTML body."""
    soup = BeautifulSoup(doc_html, "html.parser")
    seen = set()
    citations = []
    for a in soup.find_all("a", href=re.compile(r"^/doc/\d+/")):
        href = a["href"]
        cited_id = re.search(r"/doc/(\d+)/", href).group(1)
        if cited_id in seen:
            continue
        seen.add(cited_id)
        citations.append({
            "doc_id": cited_id,
            "title": a.get_text(separator=" ", strip=True),
            "url": f"https://indiankanoon.org/doc/{cited_id}/",
        })
    return citations


def summarise(row: pd.Series, data: dict) -> dict:
    citations = parse_citations(data.get("doc", ""))
    return {
        "case": row["case"],
        "source_doc_id": data.get("tid"),
        "title": data.get("title"),
        "court": data.get("docsource"),
        "date": data.get("publishdate"),
        "numcites": data.get("numcites", 0),
        "numcitedby": data.get("numcitedby", 0),
        "citations_found": len(citations),
        "citations": citations,
    }


def test_sample(n: int = 5):
    df = pd.read_excel("land_property_dispute_cases.xlsx")
    sample = df.iloc[6750: 6750 + n]

    for _, row in sample.iterrows():
        doc_id = extract_doc_id(row["link"])
        if not doc_id:
            print(f"  [SKIP] Cannot parse doc id: {row['link']}")
            continue

        print(f"\nCase : {row['case']}")
        print(f"Link : {row['link']}")
        try:
            data = fetch_doc(doc_id)
            s = summarise(row, data)
            print(f"Title: {s['title']}")
            print(f"Court: {s['court']}  |  Date: {s['date']}")
            print(f"API reports — cites: {s['numcites']}  cited_by: {s['numcitedby']}")
            print(f"Parsed from HTML — unique citations: {s['citations_found']}")
            if s["citations"]:
                print("Sample citations:")
                for c in s["citations"][:5]:
                    print(f"  [{c['doc_id']}] {c['title'][:70]}")
        except requests.HTTPError as e:
            print(f"  [HTTP {e.response.status_code}] {e.response.text[:200]}")
        except Exception as e:
            print(f"  [ERROR] {e}")


if __name__ == "__main__":
    print(f"Token loaded: {TOKEN[:8]}...")
    print("=" * 70)
    test_sample(n=5)
