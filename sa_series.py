#!/usr/bin/env python3
"""
SermonAudio series downloader.

Downloads all sermons in a series into a folder named after the series title
(or series ID as fallback).

Strategy for sermon list:
  1. Try the series RSS feed: https://feed.sermonaudio.com/<series_id>
     (this should contain ALL sermons in the series).
  2. If RSS fetch fails or returns nothing, fall back to scraping the HTML
     series page (which may only include the first ~25 sermons).
"""

import sys
import re
import pathlib
import urllib.request
import urllib.parse
import urllib.error
import html as html_module
from typing import Optional

import sa_dl  # our single-sermon downloader (low audio default, etc.)


# ---------- Basic ID / URL helpers ----------

def extract_series_id(arg: str) -> Optional[str]:
    """
    Extract the series numeric ID from:
      - a bare ID: "150726"
      - a URL: "https://www.sermonaudio.com/series/150726"
    """
    arg = arg.strip()

    if re.fullmatch(r"\d{3,}", arg):
        return arg

    if arg.startswith("http://") or arg.startswith("https://"):
        parsed = urllib.parse.urlparse(arg)
        m = re.search(r"/series/(\d+)", parsed.path)
        if m:
            return m.group(1)

    return None


def build_series_url(series_id: str) -> str:
    return f"https://www.sermonaudio.com/series/{series_id}"


# ---------- Fetch helpers ----------

def fetch_text(url: str) -> str:
    """
    Fetch text (HTML or XML) from the given URL and return it decoded as str.
    """
    print(f"[+] Fetching:\n    {url}")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        data = resp.read()
    return data.decode(charset, errors="replace")


def fetch_series_html(series_id: str) -> str:
    url = build_series_url(series_id)
    return fetch_text(url)


def fetch_series_feed(series_id: str) -> Optional[str]:
    """
    Try to fetch the RSS feed for this series.

    URL format:
      https://feed.sermonaudio.com/<series_id>

    Returns None on any error.
    """
    feed_url = f"https://feed.sermonaudio.com/{series_id}"
    try:
        text = fetch_text(feed_url)
        return text
    except urllib.error.HTTPError as e:
        print(f"[!] RSS feed HTTP error for series {series_id}: {e.code}")
    except urllib.error.URLError as e:
        print(f"[!] RSS feed URL error for series {series_id}: {e.reason}")
    except Exception as e:
        print(f"[!] RSS feed error for series {series_id}: {e}")
    return None


# ---------- Parsing helpers ----------

def extract_series_title_from_html(html: str) -> Optional[str]:
    """
    Try to get the series title from the <title> tag.

    Example:
      <title>Apologetics Individual Files | SermonAudio</title>
      -> "Apologetics Individual Files"
    """
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return None

    title = m.group(1).strip()
    title = html_module.unescape(title)
    title = re.sub(r"\s*\|\s*SermonAudio.*$", "", title, flags=re.IGNORECASE).strip()
    return title or None


def dedupe_ids(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for sid in ids:
        if sid not in seen:
            seen.add(sid)
            ordered.append(sid)
    return ordered


def extract_sermon_ids_from_series_html(html: str) -> list[str]:
    """
    From the series HTML, extract sermon IDs appearing as /sermons/<id>.
    May only contain ~25 due to lazy loading.
    """
    ids = re.findall(r"/sermons/(\d+)", html)
    return dedupe_ids(ids)


def extract_sermon_ids_from_feed(feed_xml: str) -> list[str]:
    """
    From the series RSS feed XML, extract ALL sermon IDs appearing as /sermons/<id>.
    """
    ids = re.findall(r"/sermons/(\d+)", feed_xml)
    return dedupe_ids(ids)


def infer_series_title_from_audio(path: pathlib.Path) -> Optional[str]:
    """
    Fallback: attempt to infer the series title from the first MP3's tags,
    looking at the "Comments" field (easy tags: comment/comments).
    """
    try:
        from mutagen import File as MutagenFile  # type: ignore
    except ImportError:
        print("[!] mutagen not installed; cannot infer series title from audio tags.")
        return None

    audio = MutagenFile(path, easy=True)
    if audio is None or not audio.tags:
        return None

    def first_tag(key: str) -> Optional[str]:
        v = audio.tags.get(key)
        if isinstance(v, list) and v:
            return str(v[0]).strip()
        if isinstance(v, str):
            return v.strip()
        return None

    for key in ("comment", "comments"):
        t = first_tag(key)
        if t:
            return t

    return None


# ---------- Main series downloader ----------

def download_series(series_arg: str) -> None:
    series_id = extract_series_id(series_arg)
    if series_id is None:
        print("[!] Could not extract a series ID.")
        print("    Pass either a SermonAudio series URL or a numeric ID.")
        sys.exit(1)

    # 1) Fetch HTML (for title + fallback IDs)
    html = fetch_series_html(series_id)

    # 2) Series title from HTML
    html_title = extract_series_title_from_html(html)
    if html_title:
        folder_name = sa_dl.sanitize_filename(html_title)
        print(f"[+] Series title (from HTML): {html_title}")
    else:
        folder_name = series_id
        print("[!] Could not determine series title from HTML.")
        print(f"    Using series ID as temporary folder name: {folder_name}")

    # 3) Sermon IDs: prefer RSS feed; fall back to HTML if needed
    sermon_ids: list[str] = []

    feed_xml = fetch_series_feed(series_id)
    if feed_xml:
        sermon_ids = extract_sermon_ids_from_feed(feed_xml)
        if sermon_ids:
            print(f"[+] Retrieved sermon list from RSS feed: {len(sermon_ids)} sermons.")
        else:
            print("[!] RSS feed did not contain any sermon IDs; falling back to HTML.")

    if not sermon_ids:
        sermon_ids = extract_sermon_ids_from_series_html(html)
        print(f"[+] Retrieved sermon list from HTML: {len(sermon_ids)} sermons.")

    if not sermon_ids:
        print("[!] No sermon IDs found in feed or HTML. Nothing to download.")
        sys.exit(1)

    print(f"[+] Sermon IDs in series {series_id}:")
    print("    " + ", ".join(sermon_ids))

    # 4) Output directory: series title (if known) or ID
    output_dir = pathlib.Path(folder_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[+] Downloading into folder: {output_dir.resolve()}")

    total = len(sermon_ids)
    series_title_final: Optional[str] = html_title

    for idx, sermon_id in enumerate(sermon_ids, start=1):
        print(f"\n=== [{idx}/{total}] Downloading sermon {sermon_id} ===")
        try:
            downloaded_path = sa_dl.download_audio_with_fallback(
                sermon_id,
                str(output_dir),
            )

            # If we didn't get a title from HTML, try to infer from first MP3
            if idx == 1 and series_title_final is None:
                inferred = infer_series_title_from_audio(downloaded_path)
                if inferred:
                    series_title_final = inferred
                    new_folder_name = sa_dl.sanitize_filename(inferred)
                    new_dir = output_dir.parent / new_folder_name
                    if new_dir != output_dir:
                        if new_dir.exists():
                            print(f"[!] Target folder {new_dir} already exists.")
                            print("    Keeping original folder name.")
                        else:
                            output_dir.rename(new_dir)
                            output_dir = new_dir
                            print(f"[+] Renamed series folder to:\n    {output_dir.resolve()}")

        except Exception as e:
            print(f"[!] Failed to download sermon {sermon_id}: {e}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python sa_series_dl.py <series-url-or-id>")
        print()
        print("Examples:")
        print("  python sa_series_dl.py https://www.sermonaudio.com/series/150726")
        print("  python sa_series_dl.py 150726")
        sys.exit(1)

    series_arg = sys.argv[1].strip()
    download_series(series_arg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user (Ctrl+C). Exiting.")
        sys.exit(1)
