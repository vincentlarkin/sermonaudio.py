#!/usr/bin/env python3
"""
SermonAudio downloader.

Default: high-quality audio MP3 with automatic fallback to low-quality audio.

Filenames:

  Audio high (default):
    Artist - Sermon Title_audiohq.mp3

  Audio low (fallback):
    Artist - Sermon Title_audiolow.mp3

  Video:
    Artist - Sermon Title_low.mp4
    Artist - Sermon Title_high.mp4
    Artist - Sermon Title_1080p.mp4
"""

import argparse
import pathlib
import re
import sys
import urllib.parse
import urllib.request
import urllib.error
from typing import Optional


AUDIO_BASES = {
    "high": "https://cloud.sermonaudio.com/media/audio/high",
    "low": "https://cloud.sermonaudio.com/media/audio/low",
}

VIDEO_BASES = {
    "low": "https://cloud.sermonaudio.com/media/video/low",       # 360p
    "high": "https://cloud.sermonaudio.com/media/video/high",     # 720p
    "1080p": "https://cloud.sermonaudio.com/media/video/1080p",   # 1080p
}

# Filename suffixes at the end
AUDIO_SUFFIX = {
    "high": "audiohq",
    "low": "audiolow",
}

VIDEO_SUFFIX = {
    "low": "low",
    "high": "high",
    "1080p": "1080p",
}


# ---------- ID / URL helpers ----------

def extract_sermon_id(arg: str) -> Optional[str]:
    arg = arg.strip()

    # Bare ID: just digits
    if re.fullmatch(r"\d{6,}", arg):
        return arg

    # URL case
    if arg.startswith("http://") or arg.startswith("https://"):
        parsed = urllib.parse.urlparse(arg)

        # /sermons/<id>
        m = re.search(r"/sermons/(\d+)", parsed.path)
        if m:
            return m.group(1)

        # audio .../<id>.mp3
        m = re.search(r"/media/audio/[^/]+/(\d+)\.mp3", parsed.path)
        if m:
            return m.group(1)

        # video .../<id>.mp4
        m = re.search(r"/media/video/[^/]+/(\d+)\.mp4", parsed.path)
        if m:
            return m.group(1)

    return None


def is_media_url(url: str, ext: str) -> bool:
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    parsed = urllib.parse.urlparse(url)
    return pathlib.Path(parsed.path).suffix.lower() == ext.lower()


def detect_quality_from_url(url: str, media_type: str) -> Optional[str]:
    parsed = urllib.parse.urlparse(url)
    pattern = rf"/media/{media_type}/([^/]+)/"
    m = re.search(pattern, parsed.path)
    if not m:
        return None
    q = m.group(1)
    if media_type == "audio" and q in AUDIO_BASES:
        return q
    if media_type == "video" and q in VIDEO_BASES:
        return q
    return None


def build_audio_url(sermon_id: str, quality: str) -> str:
    base = AUDIO_BASES[quality]
    return f"{base}/{sermon_id}.mp3"


def build_video_url(sermon_id: str, quality: str) -> str:
    base = VIDEO_BASES[quality]
    return f"{base}/{sermon_id}.mp4"


# ---------- Filename helpers ----------

def extract_filename_from_content_disposition(header_value: str) -> Optional[str]:
    value = header_value

    # filename*= (RFC 5987)
    m = re.search(r'filename\*\s*=\s*([^\'"]+)\'\'([^;]+)', value, flags=re.IGNORECASE)
    if m:
        filename_enc = m.group(2)
        filename = urllib.parse.unquote(filename_enc)
        return filename.strip().strip('"').strip("'")

    # filename=
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', value, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"').strip("'")

    return None


def filename_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    name = pathlib.Path(parsed.path).name
    if not name:
        return "download"
    return name


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = name.strip().rstrip(".")
    if not name:
        name = "download"
    return name


def choose_suffix(media_type: Optional[str], quality: Optional[str]) -> Optional[str]:
    if media_type == "audio" and quality in AUDIO_SUFFIX:
        return AUDIO_SUFFIX[quality]
    if media_type == "video" and quality in VIDEO_SUFFIX:
        return VIDEO_SUFFIX[quality]
    return None


# ---------- Tag-based rename for audio ----------

def maybe_rename_audio_file(path: pathlib.Path, quality: Optional[str]) -> pathlib.Path:
    """
    Try to rename an MP3 based on ID3 tags:

      Artist - Title_<suffix>.mp3

    If tags or mutagen are missing, keeps the original filename.
    """
    try:
        from mutagen import File as MutagenFile  # type: ignore
    except ImportError:
        print("[!] mutagen not installed; keeping original filename.")
        return path

    audio = MutagenFile(path, easy=True)
    if audio is None:
        return path

    def first_tag(key: str) -> Optional[str]:
        v = audio.tags.get(key) if audio.tags else None
        if isinstance(v, list) and v:
            return str(v[0])
        if isinstance(v, str):
            return v
        return None

    title = first_tag("title")
    artist = first_tag("artist") or first_tag("albumartist")

    if not title and not artist:
        return path

    parts = []
    if artist:
        parts.append(artist)
    if title:
        parts.append(title)

    base = " - ".join(parts)
    base = sanitize_filename(base)
    ext = path.suffix or ".mp3"

    suffix_label = choose_suffix("audio", quality)
    if suffix_label:
        new_name = f"{base}_{suffix_label}{ext}"
    else:
        new_name = f"{base}{ext}"

    new_path = path.with_name(new_name)
    if new_path == path:
        return path

    # Avoid accidental overwrite
    if new_path.exists():
        i = 1
        while True:
            candidate = path.with_name(f"{base}_{suffix_label}_{i}{ext}")
            if not candidate.exists():
                new_path = candidate
                break
            i += 1

    path.rename(new_path)
    print(f"[+] Renamed using tags:\n    {new_path}")
    return new_path


# ---------- Download logic ----------

def download_file(
    url: str,
    output_dir: str = ".",
    media_type: Optional[str] = None,  # "audio" | "video" | None
    quality: Optional[str] = None,     # "low" | "high" | "1080p" | None
) -> pathlib.Path:
    """
    Download a file, then (for audio) rename based on MP3 tags.

    For video we still use the header/URL filename pattern, plus suffix.
    """
    output_dir_path = pathlib.Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    print(f"[+] Downloading:\n    {url}")

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    with urllib.request.urlopen(req) as response:
        # Initial filename guess
        cd = response.headers.get("Content-Disposition")
        if cd:
            raw_name = extract_filename_from_content_disposition(cd) or filename_from_url(url)
        else:
            raw_name = filename_from_url(url)

        p = pathlib.Path(raw_name)
        base = p.stem or "download"
        ext = p.suffix.lower()

        if not ext:
            if media_type == "audio":
                ext = ".mp3"
            elif media_type == "video":
                ext = ".mp4"
            else:
                ext = ".bin"

        base = sanitize_filename(base)
        suffix_label = choose_suffix(media_type, quality)

        if suffix_label and media_type == "video":
            filename = f"{base}_{suffix_label}{ext}"
        else:
            filename = f"{base}{ext}"

        output_path = output_dir_path / filename

        print(f"[+] Saving as (initial):\n    {output_path}")

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

    print("\n[+] Download complete.")

    # For audio, try to rename using tags
    if media_type == "audio":
        return maybe_rename_audio_file(output_path, quality)

    return output_path


def download_audio_with_fallback(sermon_id: str, output_dir: str = ".") -> pathlib.Path:
    """
    Try high-quality audio first, then fall back to low if high isn't available.
    """
    last_error: Optional[Exception] = None
    for quality in ("high", "low"):
        url = build_audio_url(sermon_id, quality)
        print(f"[+] Trying {quality} quality audio...")
        try:
            return download_file(url, output_dir, media_type="audio", quality=quality)
        except urllib.error.HTTPError as e:
            print(f"[!] {quality} audio failed: HTTP {e.code}")
            last_error = e
        except urllib.error.URLError as e:
            print(f"[!] {quality} audio failed: {e.reason}")
            last_error = e

    print("[!] Could not download audio in any quality.")
    if last_error:
        raise last_error
    else:
        raise RuntimeError("Audio download failed for unknown reasons.")


# ---------- Main CLI ----------

def main() -> None:
    parser = argparse.ArgumentParser(description="SermonAudio downloader (audio/video).")
    parser.add_argument(
        "input",
        help="Sermon URL, direct media URL, or numeric sermon ID",
    )
    parser.add_argument(
        "--video",
        choices=["low", "high", "1080p"],
        help="Download video instead of audio at the given quality",
    )
    parser.add_argument(
        "-o",
        "--out",
        default=".",
        help="Output directory (default: current directory)",
    )

    args = parser.parse_args()
    user_input = args.input.strip()

    # VIDEO MODE
    if args.video:
        if is_media_url(user_input, ".mp4"):
            q = detect_quality_from_url(user_input, "video") or args.video
            download_file(user_input, args.out, media_type="video", quality=q)
            return

        sermon_id = extract_sermon_id(user_input)
        if sermon_id is None:
            print("[!] Could not extract a sermon ID for video download.")
            sys.exit(1)

        url = build_video_url(sermon_id, args.video)
        download_file(url, args.out, media_type="video", quality=args.video)
        return

    # AUDIO MODE (default)
    if is_media_url(user_input, ".mp3"):
        q = detect_quality_from_url(user_input, "audio") or "high"
        download_file(user_input, args.out, media_type="audio", quality=q)
        return

    sermon_id = extract_sermon_id(user_input)
    if sermon_id is None:
        print("[!] Could not extract a sermon ID for audio download.")
        sys.exit(1)

    download_audio_with_fallback(sermon_id, args.out)


if __name__ == "__main__":
    main()
