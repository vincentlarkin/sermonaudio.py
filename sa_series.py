#!/usr/bin/env python3
"""
SermonAudio series downloader.

Given a series URL like:
  https://www.sermonaudio.com/series/200129

or just the numeric ID:
  200129

This script will:

  1. Fetch the series page.
  2. Extract the series title from the HTML <title> tag.
  3. Extract all sermon IDs (/sermons/<id> links).
  4. Create a folder named after the series title
     (fallback: series ID if title can't be found).
  5. For each sermon ID, call sa_dl.download_audio_with_fallback(...)
     to download high-quality audio with low-quality fallback.

If the HTML title is missing or broken, the script will also try to infer
a series title from the first MP3's tags, looking in the "Comments"
field, and then rename the folder accordingly.
"""

import sys
import re
import pathlib
import urllib.request
import urllib.parse
import urllib.error
import html as html_module
from typing import Optional

# Import your existing single-sermon downloader
import sa_dl  # type: ignore


# ---------- Helpers ----------

def extract_series_id(arg: str) -> Optional[str]:
    """
    Extract the series numeric ID from:
      - a bare ID: "200129"
      - a URL: "https://www.sermonaudio.com/series/200129"
    """
    arg = arg.strip()

    # Bare ID: just digits
    if re.fullmatch(r"\d{3,}", arg):
        return arg

    # URL case
    if arg.startswith("http://") or arg.startswith("https://"):
        parsed = urllib.parse.urlparse(arg)
        # e.g. /series/200129 or /series/200129/...
        m = re.search(r"/series/(\d+)", parsed.path)
        if m:
            return m.group(1)

    return None


def build_series_url(series_id: str) -> str:
    """
    Construct a canonical series URL from the ID.
    """
    return f"https://www.sermonaudio.com/series/{series_id}"


def fetch_html(url: str) -> str:
    """
    Fetch HTML from the given URL and return it as a decoded string.
    """
    print(f"[+] Fetching series page:\n    {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        data = resp.read()
    html = data.decode(charset, errors="replace")
    return html


def extract_series_title_from_html(html: str) -> Optional[str]:
    """
    Try to get the series title from the <title> tag.

    Example:
      <title>Freedom In The Gospel | SermonAudio</title>
      -> "Freedom In The Gospel"
    """
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return None

    title = m.group(1).strip()
    title = html_module.unescape(title)

    # Strip " | SermonAudio" or similar suffix
    title = re.sub(r"\s*\|\s*SermonAudio.*$", "", title, flags=re.IGNORECASE).strip()
    if not title:
        return None

    return title


def extract_sermon_ids_from_series_html(html: str) -> list[str]:
    """
    From the series HTML, extract all sermon IDs appearing as /sermons/<id>.
    Returns a list of unique IDs, preserving their first-seen order.
    """
    ids = re.findall(r"/sermons/(\d+)", html)
    seen: set[str] = set()
    ordered: list[str] = []
    for sid in ids:
        if sid not in seen:
            seen.add(sid)
            ordered.append(sid)
    return ordered


def infer_series_title_from_audio(path: pathlib.Path) -> Optional[str]:
    """
    Fallback: attempt to infer the series title from the first MP3's tags.

    Specifically, look at the "Comments" tag (ID3 COMM), which
    appears as "comment" or "comments" in mutagen's easy tags.
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

    # Try comments first (what you see in Windows "Comments")
    for key in ("comment", "comments"):
        t = first_tag(key)
        if t:
            return t

    return None


# ---------- Main series downloader ----------

def download_series(series_arg: str) -> None:
    """
    Orchestrate the full series download.
    """
    series_id = extract_series_id(series_arg)
    if series_id is None:
        print("[!] Could not extract a series ID.")
        print("    Pass either a SermonAudio series URL or a numeric ID.")
        sys.exit(1)

    series_url = build_series_url(series_id)
    html = fetch_html(series_url)

    # 1) Try to get a pretty series title from HTML <title>
    html_title = extract_series_title_from_html(html)

    if html_title:
        folder_name = sa_dl.sanitize_filename(html_title)
        print(f"[+] Series title (from HTML): {html_title}")
    else:
        folder_name = series_id
        print("[!] Could not determine series title from HTML.")
        print(f"    Using series ID as temporary folder name: {folder_name}")

    sermon_ids = extract_sermon_ids_from_series_html(html)
    if not sermon_ids:
        print("[!] No sermon IDs found on this series page.")
        sys.exit(1)

    print(f"[+] Found {len(sermon_ids)} sermons in series {series_id}:")
    print("    " + ", ".join(sermon_ids))

    # Output directory: series title (if known) or ID
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
        print("  python sa_series_dl.py https://www.sermonaudio.com/series/200129")
        print("  python sa_series_dl.py 200129")
        sys.exit(1)

    series_arg = sys.argv[1].strip()
    download_series(series_arg)


if __name__ == "__main__":
    main()
