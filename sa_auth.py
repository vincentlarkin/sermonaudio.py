#!/usr/bin/env python3
"""
sa_auth.py

Handles fetching and storing the SermonAudio API key.
Strategies:
1. Check 'auth.txt' for a cached key.
2. Validate the cached key against the API.
3. If missing or invalid, fetch https://www.sermonaudio.com/ and scrape the key.
4. Save the new key to 'auth.txt'.
"""

import os
import re
import time
import requests

API_KEY_FILE = "auth.txt"
BASE_URL = "https://www.sermonaudio.com/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SA-Auth-Scraper/1.0)",
}

def validate_key(key: str) -> bool:
    """
    Checks if the API key works by making a minimal request.
    Returns True if HTTP 200, False otherwise.
    """
    test_url = "https://api.sermonaudio.com/v2/node/sermons"
    # Minimal params to get a quick valid/invalid response
    params = {"pageSize": "1", "liteBroadcaster": "true"}
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SA-Auth-Validator/1.0)",
        "X-API-Key": key
    }
    
    try:
        # Short timeout so we don't hang startup too long
        resp = requests.get(test_url, params=params, headers=headers, timeout=5)
        if resp.status_code == 200:
            return True
        else:
            print(f"[auth] Key validation failed: HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"[auth] Warning: Validation request failed ({e}). Assuming key is potentially bad or network is down.")
        return False

def fetch_new_key() -> str:
    """
    Scrapes the SermonAudio homepage for the embedded Nuxt API key.
    """
    print(f"[auth] Fetching new API key from {BASE_URL}...")
    try:
        resp = requests.get(BASE_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"[auth] Failed to fetch homepage: {e}")
        raise

    # Pattern: apiKey:"3C2E7B5F-5E3C-4AAC-AF49-0906CBDA920F"
    # Note: The key format is UUID-like.
    m = re.search(r'apiKey:"([A-F0-9-]+)"', html)
    if not m:
        print("[auth] Could not find apiKey in page source.")
        raise ValueError("Could not extract API key from SermonAudio homepage.")

    key = m.group(1)
    print(f"[auth] Found key: {key}")
    
    # Save to file
    try:
        with open(API_KEY_FILE, "w", encoding="utf-8") as f:
            f.write(key)
        print(f"[auth] Saved key to {API_KEY_FILE}")
    except Exception as e:
        print(f"[auth] Warning: could not save {API_KEY_FILE}: {e}")

    return key

def get_api_key(force_refresh: bool = False) -> str:
    """
    Returns the API key.
    - If force_refresh is True, fetches a new one immediately.
    - If auth.txt exists, reads it AND validates it.
    - If invalid or missing, fetches a new one.
    """
    key = None
    
    if not force_refresh and os.path.exists(API_KEY_FILE):
        try:
            with open(API_KEY_FILE, "r", encoding="utf-8") as f:
                key = f.read().strip()
            
            if key:
                # 1. Basic format check
                if not re.match(r"^[A-F0-9-]{30,}$", key):
                    print("[auth] Stored key has invalid format.")
                    key = None
                # 2. Liveness check
                elif not validate_key(key):
                    print("[auth] Stored key is expired or invalid.")
                    key = None
                
        except Exception as e:
            print(f"[auth] Error reading {API_KEY_FILE}: {e}")
            key = None

    if key:
        return key

    # If we are here, we either need a refresh or the stored key was bad
    return fetch_new_key()

if __name__ == "__main__":
    try:
        print("[auth] Getting API Key (checking cache + validating)...")
        k = get_api_key()
        print(f"[auth] Active API Key: {k}")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
