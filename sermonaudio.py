#!/usr/bin/env python3
"""
SermonAudio downloader (MP3, 'low' quality).

Supports:
  - Sermon page URL:
      python sa_dl.py https://www.sermonaudio.com/sermons/1125241428175438
  - Direct MP3 URL:
      python sa_dl.py "https://cloud.sermonaudio.com/media/audio/low/1125241428175438.mp3?ts=1732549366&language=eng&download=true"
  - Bare sermon ID:
      python sa_dl.py 1125241428175438
"""

import sys
import re
import pathlib
import urllib.request
import urllib.parse


CLOUD_BASE = "https://cloud.sermonaudio.com/media/audio/low"


def extract_sermon_id(arg: str) -> str | None:
    """
    Given a sermon page URL, a direct MP3 URL, or a bare ID,
    return the numeric sermon ID as a string, or None if we can't.
    """
    arg = arg.strip()

    # Bare ID: just digits
    if re.fullmatch(r"\d{6,}", arg):
        return arg

    # URL case
    if arg.startswith("http://") or arg.startswith("https://"):
        parsed = urllib.parse.urlparse(arg)

        # Try to match /sermons/<id> in the path
        m = re.search(r"/sermons/(\d+)", parsed.path)
        if m:
            return m.group(1)

        # Direct MP3 URL: .../low/<id>.mp3
        m = re.search(r"/media/audio/[^/]+/(\d+)\.mp3", parsed.path)
        if m:
            return m.group(1)

    # Could not figure it out
    return None


def build_mp3_url(sermon_id: str) -> str:
    """
    Construct the canonical MP3 URL from the sermon ID.
    """
    return f"{CLOUD_BASE}/{sermon_id}.mp3"


def filename_from_url(url: str) -> str:
    """
    Get a sensible filename from a URL, falling back to 'download.mp3'.
    Strips query parameters.
    """
    parsed = urllib.parse.urlparse(url)
    name = pathlib.Path(parsed.path).name
    if not name:
        name = "download.mp3"

    # Ensure .mp3 extension
    if not name.lower().endswith(".mp3"):
        name += ".mp3"

    return name


def download_file(url: str, output_dir: str = ".") -> pathlib.Path:
    """
    Stream-download the file at 'url' into 'output_dir'.
    Returns the full pathlib.Path of the saved file.
    """
    output_dir_path = pathlib.Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    filename = filename_from_url(url)
    output_path = output_dir_path / filename

    print(f"[+] Downloading:\n    {url}")
    print(f"[+] Saving as:\n    {output_path}")

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    with urllib.request.urlopen(req) as response:
        total_size_header = response.headers.get("Content-Length")
        total_size = int(total_size_header) if total_size_header is not None else None

        if total_size is not None:
            print(f"[+] File size: {total_size / 1024:.1f} KB")

        chunk_size = 8192
        bytes_downloaded = 0

        with open(output_path, "wb") as out_file:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                out_file.write(chunk)
                bytes_downloaded += len(chunk)

                if total_size:
                    percent = bytes_downloaded * 100 // total_size
                    print(f"\r[+] Downloaded: {percent}%", end="", flush=True)

    print("\n[+] Done.")
    return output_path


def main():
    if len(sys.argv) < 2:
        print("Usage: python sa_dl.py <sermon-url | mp3-url | sermon-id>")
        print()
        print("Examples:")
        print("  python sa_dl.py https://www.sermonaudio.com/sermons/1125241428175438")
        print('  python sa_dl.py "https://cloud.sermonaudio.com/media/audio/low/1125241428175438.mp3?ts=1732549366&language=eng&download=true"')
        print("  python sa_dl.py 1125241428175438")
        sys.exit(1)

    arg = sys.argv[1]

    sermon_id = extract_sermon_id(arg)
    if sermon_id is None:
        print("[!] Could not extract a sermon ID from your input.")
        print("    Make sure you passed either a SermonAudio sermon URL, a direct MP3 URL, or a numeric ID.")
        sys.exit(1)

    mp3_url = build_mp3_url(sermon_id)

    # If user actually gave a direct MP3 URL, we could also choose to use that instead.
    # For now: always use our canonical URL (simpler, no query params).
    download_file(mp3_url)


if __name__ == "__main__":
    main()
