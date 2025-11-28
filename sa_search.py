#!/usr/bin/env python3
"""
sa_search.py

Search SermonAudio for broadcasters, speakers, sermons, and series.
Usage:
    python sa_search.py "paul washer"
"""

import sys
import argparse
import requests
import json

try:
    import sa_auth
except ImportError:
    sa_auth = None

BASE_URL = "https://api.sermonaudio.com/v2/node/search"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SA-Search-DL/1.0)",
}

session = requests.Session()
session.headers.update(HEADERS)

def ensure_api_key():
    if "X-API-Key" in session.headers:
        return

    key = None
    if sa_auth:
        try:
            key = sa_auth.get_api_key()
        except Exception as e:
            print(f"[!] Auth error: {e}")
    
    if not key:
        # Fallback known key
        key = "3C2E7B5F-5E3C-4AAC-AF49-0906CBDA920F"
        print("[!] Using fallback hardcoded API key.")

    session.headers["X-API-Key"] = key

def perform_search(query: str, sort_by: str = None) -> dict:
    ensure_api_key()
    params = {
        "query": query,
        "liteBroadcaster": "true",
        "pageSize": "10",  # We don't need too many for a summary
    }
    if sort_by:
        params["sortBy"] = sort_by

    try:
        resp = session.get(BASE_URL, params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[!] Search failed: {e}")
        return {}

def get_sermon_info(sermon_id: str) -> dict:
    ensure_api_key()
    url = f"https://api.sermonaudio.com/v2/node/sermons/{sermon_id}"
    try:
        resp = session.get(url, timeout=20)
        if resp.status_code == 404:
            print(f"[!] Sermon {sermon_id} not found.")
            return {}
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[!] Failed to get sermon info: {e}")
        return {}

def print_broadcasters(results):
    if not results:
        return
    print("\n--- Broadcasters ---")
    for b in results[:3]:
        name = b.get('displayName', 'Unknown')
        loc = b.get('location', 'Unknown Location')
        print(f"* {name} ({loc}) [ID: {b.get('broadcasterID')}]")

def print_speakers(results):
    if not results:
        return
    print("\n--- Speakers ---")
    for s in results[:3]:
        name = s.get('displayName', 'Unknown')
        count = s.get('sermonCount', 0)
        print(f"* {name} ({count} sermons) [ID: {s.get('speakerID')}]")

def print_series(results):
    if not results:
        return
    print("\n--- Series ---")
    for s in results[:3]:
        title = s.get('title', 'Untitled')
        broadcaster = s.get('broadcaster', {}).get('displayName', 'Unknown')
        count = s.get('count', 0)
        print(f"* {title} ({broadcaster}) - {count} sermons")

def print_sermons(results, label="Top Sermons"):
    if not results:
        return
    print(f"\n--- {label} ---")
    for s in results[:5]:
        title = s.get('fullTitle', s.get('displayTitle', 'Untitled'))
        speaker = s.get('speaker', {}).get('displayName', 'Unknown')
        broadcaster = s.get('broadcaster', {}).get('displayName', 'Unknown')
        date = s.get('preachDate', 'Unknown Date')
        print(f"* {title}")
        print(f"  by {speaker} | {broadcaster} | {date}")
        print(f"  ID: {s.get('sermonID')}")

def main():
    parser = argparse.ArgumentParser(description="Search SermonAudio.")
    parser.add_argument("query", nargs="+", help="Search query (e.g. 'paul washer')")
    args = parser.parse_args()
    
    query = " ".join(args.query)
    print(f"[+] Searching for: '{query}'")

    # 1. Default Search (Top Results)
    data_top = perform_search(query)
    
    print_broadcasters(data_top.get('broadcasterResults', []))
    print_speakers(data_top.get('speakerResults', []))
    print_series(data_top.get('seriesResults', []))
    print_sermons(data_top.get('sermonResults', []), "Top Sermons")

    # 2. Newest Search
    # We use 'newest-published' based on API error message suggestion
    data_new = perform_search(query, sort_by="newest-published")
    print_sermons(data_new.get('sermonResults', []), "Newest Sermons")

if __name__ == "__main__":
    main()

