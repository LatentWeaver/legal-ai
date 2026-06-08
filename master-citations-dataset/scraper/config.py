"""Indian Kanoon API configuration."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from this directory
load_dotenv(Path(__file__).parent / ".env")

TOKEN = os.environ.get("INDIANKANOON_TOKEN", "")
if not TOKEN or TOKEN == "your_token_here":
    print("ERROR: Set INDIANKANOON_TOKEN in indiankanoon/.env")
    sys.exit(1)

BASE_URL = "https://api.indiankanoon.org"
HEADERS = {
    "Authorization": f"Token {TOKEN}",
    "Accept": "application/json",
}

# Endpoints
SEARCH_URL = f"{BASE_URL}/search/"
DOC_URL = f"{BASE_URL}/doc/"        # + <docid>/
DOCMETA_URL = f"{BASE_URL}/docmeta/"  # + <docid>/
