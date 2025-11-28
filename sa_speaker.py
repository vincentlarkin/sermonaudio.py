#!/usr/bin/env python3
"""
sa_speaker.py

Download all sermons for a SermonAudio speaker.

- Input: speaker URL or ID, e.g.
      python sa_speaker.py https://www.sermonaudio.com/speakers/11657/
      python sa_speaker.py 11657
- Folder structure: SpeakerName/[SeriesTitle]/Sermon Title.mp3
- Audio: low quality by default, fallback to high.
- Uses the node/sermons endpoint (auto-discovered), with HTML fallback.
"""

import os
import re
import sys
import time
import json
from typing import List, Set, Tuple, Optional

import requests
from bs4 import BeautifulSoup

try:
    import sa_auth
except ImportError:
    # Fallback if sa_auth is not found, or user can just run this standalone with a hardcoded key
    sa_auth = None

BASE_URL = "https://www.sermonaudio.com"

# Initial headers; X-API-Key will be injected dynamically if sa_auth is present
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SA-Speaker-DL/1.0)",
}

session = requests.Session()
session.headers.update(HEADERS)

def ensure_api_key():
    """
    Ensure X-API-Key is in session headers.
    Uses sa_auth if available, otherwise falls back to a hardcoded known key.
    """
    if "X-API-Key" in session.headers:
        return

    key = None
    if sa_auth:
        try:
            key = sa_auth.get_api_key()
        except Exception as e:
            print(f"[!] Auth error: {e}")
    
    if not key:
        # Fallback known key (as of late 2025)
        key = "3C2E7B5F-5E3C-4AAC-AF49-0906CBDA920F"
        print("[!] Using fallback hardcoded API key.")

    session.headers["X-API-Key"] = key


# ---------- basic helpers ----------

def slugify(name: str) -> str:
    """Safe for filenames/folders."""
    if not name:
        return "untitled"
    name = name.strip()
    name = name.replace(":", " -")
    name = re.sub(r"[\\/*?\"<>|]+", "", name)
    name = re.sub(r"\s+", " ", name)
    return name or "untitled"


def extract_speaker_id(arg: str) -> str:
    """Accept bare ID or /speakers/<id>/ URL."""
    arg = arg.strip()
    if arg.isdigit():
        return arg
    m = re.search(r"/speakers/(\d+)", arg)
    if not m:
        raise ValueError(f"Could not extract speaker ID from: {arg}")
    return m.group(1)


def fetch_html(url: str) -> str:
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    return resp.text


# ---------- speaker name ----------

def get_speaker_name(speaker_id: str) -> str:
    """Fetch the speaker sermons page and grab a nice display name."""
    url = f"{BASE_URL}/speakers/{speaker_id}/sermons"
    print(f"[+] Fetching speaker page for name: {url}")
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        name = h1.get_text(strip=True)
        return re.sub(r"^#", "", name).strip()

    title = soup.title.string if soup.title else ""
    if title:
        title = title.strip()
        # e.g. "Sermons | Dr. Mark Minnick | SermonAudio"
        m = re.search(r"\|\s*(.+?)\s*\|\s*SermonAudio", title)
        if m:
            return m.group(1).strip()

    return f"Speaker {speaker_id}"


# ---------- node/sermons discovery & pagination ----------

NODE_CANDIDATES = [
    "https://api.sermonaudio.com/v2/node/sermons",
    "/node/sermons",
    "/api/node/sermons",
]


def extract_sermon_ids_from_node_response(text: str) -> List[str]:
    """
    The node/sermons response is usually JSON from the API, but could be HTML fallback.
    We try to parse as JSON first, looking for 'results' -> 'sermonID'.
    Fallback to regex for HTML.
    """
    ids = []
    
    # 1. Try JSON parsing
    try:
        data = json.loads(text)
        # Check if it has 'results' list
        if isinstance(data, dict) and "results" in data:
            for item in data["results"]:
                if "sermonID" in item:
                    # sermonID could be int or str
                    ids.append(str(item["sermonID"]))
    except json.JSONDecodeError:
        # Not JSON, ignore
        pass

    # 2. If JSON failed or returned nothing, try regex (fallback for HTML or weird formats)
    if not ids:
        # Look for /sermons/<id> (HTML or API URLs)
        ids_regex = re.findall(r"/sermons/(\d+)", text)
        ids.extend(ids_regex)
        
        # Look for "sermonID": "<id>" or "sermonID": <id> (JSON-like in text)
        ids_regex2 = re.findall(r'"sermonID":\s*"?(\d+)"?', text)
        ids.extend(ids_regex2)

    seen: Set[str] = set()
    ordered: List[str] = []
    for sid in ids:
        if sid not in seen:
            seen.add(sid)
            ordered.append(sid)
    return ordered


def discover_node_base(speaker_id: str) -> Optional[str]:
    """
    Try likely node/sermons URLs and return the first one that works.
    """
    test_params = {
        "sortBy": "newest",
        "requireAudio": "false",
        "speakerID": speaker_id,
        "pageSize": "25",
        "page": "1",
        "liteBroadcaster": "true",
        "cacheLanguage": "en",
        "cache": "true",
    }

    for path in NODE_CANDIDATES:
        if path.startswith("http"):
            url = path
        else:
            url = BASE_URL + path
        
        print(f"[+] Probing node endpoint: {url}")
        try:
            resp = session.get(url, params=test_params, timeout=20)
        except Exception as e:
            print(f"[!] Error probing {url}: {e}")
            continue

        if resp.status_code == 404:
            print(f"[!] {url} returned 404, skipping.")
            continue

        if resp.status_code != 200:
            print(f"[!] {url} returned HTTP {resp.status_code}, skipping.")
            continue

        ids = extract_sermon_ids_from_node_response(resp.text)
        if ids:
            print(f"[+] {url} looks good (found {len(ids)} sermon IDs on test).")
            return url
        else:
            print(f"[!] {url} returned 200 but no sermon IDs, skipping.")

    return None


def collect_sermon_ids_via_node(speaker_id: str,
                                page_size: int = 100,
                                max_pages: int = 50) -> List[str]:
    """
    Call discovered node/sermons endpoint page=1..N until no new IDs.
    """
    ensure_api_key()
    base_url = discover_node_base(speaker_id)
    if not base_url:
        print("[!] Could not discover a working node/sermons endpoint.")
        return []

    all_ids: List[str] = []
    seen: Set[str] = set()

    for page in range(1, max_pages + 1):
        params = {
            "sortBy": "newest",
            "requireAudio": "false",
            "speakerID": speaker_id,
            "pageSize": str(page_size),
            "page": str(page),
            "liteBroadcaster": "true",
            "cacheLanguage": "en",
            "cache": "true",
        }
        print(f"[+] node/sermons page {page} (pageSize={page_size})")
        try:
            resp = session.get(base_url, params=params, timeout=20)
            if resp.status_code != 200:
                print(f"[!] HTTP {resp.status_code} on node page {page}, stopping.")
                break
        except Exception as e:
            print(f"[!] Error calling node page {page}: {e}")
            break

        page_ids = extract_sermon_ids_from_node_response(resp.text)
        new_ids = [sid for sid in page_ids if sid not in seen]
        print(f"[+] Page {page}: {len(new_ids)} new sermons "
              f"(total so far: {len(seen) + len(new_ids)})")

        if not page_ids:
            break

        all_ids.extend(new_ids)
        seen.update(new_ids)

        # If server returns less than a full page, likely last.
        # Check RAW page count (page_ids), not deduped count (new_ids)
        if len(page_ids) < page_size:
            break

        time.sleep(0.35)

    return all_ids


# ---------- HTML fallback (if node fails completely) ----------

def collect_sermon_ids_via_html(speaker_id: str,
                                max_pages: int = 10) -> List[str]:
    """
    Old method: scrape /speakers/<id>/sermons?page=N
    """
    all_ids: List[str] = []
    seen: Set[str] = set()

    for page in range(1, max_pages + 1):
        url = f"{BASE_URL}/speakers/{speaker_id}/sermons"
        if page > 1:
            url += f"?page={page}"
        print(f"[+] Fetching HTML page {page}: {url}")
        try:
            html_text = fetch_html(url)
        except Exception as e:
            print(f"[!] Error fetching HTML page {page}: {e}")
            break

        ids = re.findall(r"/sermons/(\d+)", html_text)
        new_ids = [sid for sid in ids if sid not in seen]
        print(f"[+] HTML page {page}: {len(new_ids)} new sermons "
              f"(total so far: {len(seen) + len(new_ids)})")

        if not new_ids:
            break

        all_ids.extend(new_ids)
        seen.update(new_ids)

        time.sleep(0.35)

    return all_ids


# ---------- sermon metadata & audio ----------

def fetch_sermon_page(sermon_id: str) -> Tuple[str, Optional[str]]:
    """
    Return (sermon_title, series_title_or_None) by scraping the sermon page.
    """
    url = f"{BASE_URL}/sermons/{sermon_id}"
    print(f"[+] Fetching sermon page: {url}")
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title = "Untitled Sermon"
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(" ", strip=True)
    elif soup.title and soup.title.string:
        t = soup.title.string.strip()
        t = re.sub(r"\s*\|\s*SermonAudio.*$", "", t)
        if t:
            title = t

    # Series
    series_title = None
    a = soup.find("a", href=re.compile(r"/series/\d+"))
    if a:
        series_title = a.get_text(" ", strip=True) or None

    return title, series_title


def download_file(url: str, dest_path: str) -> bool:
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    print(f"[+] Downloading:\n    {url}")
    with session.get(url, stream=True, timeout=60) as r:
        if r.status_code != 200:
            print(f"[!] HTTP {r.status_code} for {url}")
            return False
        total = int(r.headers.get("Content-Length") or 0)
        downloaded = 0
        chunk_size = 8192
        tmp_path = dest_path + ".part"
        print(f"[+] Saving as:\n    {dest_path}")
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                f.write(chunk)
                if total:
                    downloaded += len(chunk)
                    pct = downloaded * 100.0 / total
                    sys.stdout.write(
                        f"\r    Downloaded: {pct:5.1f}% "
                        f"({downloaded/1024:.1f} / {total/1024:.1f} KB)"
                    )
                    sys.stdout.flush()
        if total:
            print()
        os.replace(tmp_path, dest_path)
    return True


def download_sermon_audio(sermon_id: str,
                          root_folder: str,
                          speaker_name: str) -> Optional[str]:
    """
    Folder structure:
        root / Speaker / [Series] / Sermon Title.mp3

    File naming:
        - low (default): "Sermon Title.mp3"
        - if low fails and high works, still "Sermon Title.mp3"
    """
    title, series_title = fetch_sermon_page(sermon_id)

    speaker_folder = slugify(speaker_name)
    if series_title:
        series_folder = slugify(series_title)
        base_dir = os.path.join(root_folder, speaker_folder, series_folder)
    else:
        base_dir = os.path.join(root_folder, speaker_folder)

    filename = slugify(title) + ".mp3"
    dest_path = os.path.join(base_dir, filename)

    if os.path.exists(dest_path):
        print(f"[=] Already exists, skipping: {dest_path}")
        return dest_path

    url_low = f"https://cloud.sermonaudio.com/media/audio/low/{sermon_id}.mp3?download=true"
    print(f"[+] Trying low quality audio for {sermon_id}...")
    if download_file(url_low, dest_path):
        print(f"[+] Downloaded sermon {sermon_id} -> {dest_path}")
        return dest_path

    url_high = f"https://cloud.sermonaudio.com/media/audio/high/{sermon_id}.mp3?download=true"
    print(f"[!] Low failed, trying high quality audio...")
    if download_file(url_high, dest_path):
        print(f"[+] Downloaded sermon {sermon_id} (high) -> {dest_path}")
        return dest_path

    print(f"[!] Failed to download sermon {sermon_id} in any audio quality.")
    return None


# ---------- main ----------

def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python sa_speaker.py <speaker_url_or_id>")
        return 1

    raw = sys.argv[1].strip()
    try:
        speaker_id = extract_speaker_id(raw)
    except ValueError as e:
        print(f"[!] {e}")
        return 1

    speaker_name = get_speaker_name(speaker_id)
    print(f"[+] Speaker: {speaker_name} (ID: {speaker_id})")

    # 1) Try node/sermons (preferred)
    sermon_ids = collect_sermon_ids_via_node(speaker_id)

    # 2) If node fails entirely, fallback to HTML
    if not sermon_ids:
        print("[!] Falling back to HTML pagination.")
        sermon_ids = collect_sermon_ids_via_html(speaker_id)

    if not sermon_ids:
        print("[!] No sermons found; nothing to do.")
        return 1

    # Deduplicate while preserving order
    unique_ids: List[str] = []
    seen: Set[str] = set()
    for sid in sermon_ids:
        if sid not in seen:
            seen.add(sid)
            unique_ids.append(sid)

    print(f"[+] Total sermons found: {len(unique_ids)}")
    print("    " + ", ".join(unique_ids))

    root_folder = os.path.abspath(".")
    print(f"[+] Root download folder:\n    {root_folder}")

    for idx, sid in enumerate(unique_ids, start=1):
        print(f"\n=== [{idx}/{len(unique_ids)}] Downloading sermon {sid} ===")
        try:
            download_sermon_audio(sid, root_folder, speaker_name)
        except KeyboardInterrupt:
            print("\n[!] Interrupted by user (Ctrl+C). Exiting.")
            return 1
        except Exception as e:
            print(f"[!] Error downloading sermon {sid}: {e}")

    print("\n[+] Done.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user (Ctrl+C). Exiting.")
        sys.exit(1)
