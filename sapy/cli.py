"""
sapy command line.

  sapy                          interactive: search or paste links, pick, download
  sapy <link | sermon ID>       download whatever the link points to
  sapy <words...>               search, then pick from the results
  sapy speaker "paul washer"    everything by a speaker (name or ID)
  sapy series 36435             a whole series
  sapy broadcaster ghbc         a whole church/broadcaster catalog
  sapy search <words...>        just show search results
  sapy config [set|unset KEY VALUE]
  sapy gui                      desktop app (pip install "sermonaudio.py[gui]")
"""

import argparse
import os
import shutil
import sys
from typing import List, Optional

from . import __version__, api, config
from .download import (AUDIO_QUALITIES, VIDEO_QUALITIES, Sermon, broadcaster_sermons, download_many,
                       download_sermon, existing_path, series_sermons, single_sermon, speaker_sermons)
from .resolve import KINDS, Target, resolve

COMMANDS = ("search", "config", "gui") + KINDS


def interactive_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def confirm(prompt: str, opts) -> bool:
    if opts.yes or not interactive_tty():
        return True
    return ask(f"{prompt} [Y/n] ").lower() in ("", "y", "yes")


# ---------- search ----------

def search_entries(query: str, sort_by: Optional[str] = None) -> List[tuple]:
    """[(group, label, Target)] across speakers, series, broadcasters, sermons."""
    data = api.search(query, sort_by=sort_by)
    entries = []
    for s in (data.get("speakerResults") or [])[:5]:
        n = s.get("sermonCount")
        entries.append(("Speakers", f"{s.get('displayName')}" + (f" · {n:,} sermons" if isinstance(n, int) else ""),
                        Target("speaker", str(s.get("speakerID")))))
    for s in (data.get("seriesResults") or [])[:5]:
        b = (s.get("broadcaster") or {}).get("displayName", "")
        n = s.get("count")
        entries.append(("Series", f"{s.get('title')} · {b}" + (f" · {n} sermons" if n else ""),
                        Target("series", str(s.get("seriesID")))))
    for b in (data.get("broadcasterResults") or [])[:3]:
        loc = b.get("location") or ""
        entries.append(("Broadcasters", f"{b.get('displayName')}" + (f" ({loc})" if loc else ""),
                        Target("broadcaster", str(b.get("broadcasterID")))))
    for s in (data.get("sermonResults") or [])[:8]:
        sp = (s.get("speaker") or {}).get("displayName", "")
        date = str(s.get("preachDate") or "")[:10]
        entries.append(("Sermons", f"{s.get('fullTitle') or s.get('displayTitle')} — {sp}, {date}",
                        Target("sermon", str(s.get("sermonID")))))
    return entries


def show_search(query: str, sort_by: Optional[str] = None) -> List[tuple]:
    """Print numbered results; returns [(label, Target)] in display order."""
    print(f"Searching for “{query}”…")
    entries, last = [], None
    for group, label, target in search_entries(query, sort_by):
        if group != last:
            print(f"\n {group}")
            last = group
        entries.append((label, target))
        print(f"  {len(entries):>2}  {label}")
    if not entries:
        print("  No results.")
    return entries


def pick_from_search(query: str, opts) -> Optional[Target]:
    entries = show_search(query, "newest-published" if opts.newest else None)
    if not entries or not interactive_tty():
        return None
    choice = ask("\nPick a number (Enter to cancel): ")
    if choice.isdigit() and 1 <= int(choice) <= len(entries):
        return entries[int(choice) - 1][1]
    return None


def speaker_id_for_name(name: str, opts) -> Optional[str]:
    data = api.search(name)
    speakers = data.get("speakerResults") or []
    if not speakers:
        print(f"No speaker found for “{name}”.")
        return None
    exact = [s for s in speakers if (s.get("displayName") or "").lower() == name.lower()]
    if exact or len(speakers) == 1 or not interactive_tty() or opts.yes:
        s = (exact or speakers)[0]
        return str(s.get("speakerID"))
    print(f"Speakers matching “{name}”:")
    for i, s in enumerate(speakers[:8], 1):
        print(f"  {i}  {s.get('displayName')} · {s.get('sermonCount', '?')} sermons")
    choice = ask("Which one? ")
    if choice.isdigit() and 1 <= int(choice) <= min(8, len(speakers)):
        return str(speakers[int(choice) - 1].get("speakerID"))
    return None


# ---------- downloading ----------

def _bar(done: int, total: int) -> None:
    width = max(10, min(40, shutil.get_terminal_size().columns - 30))
    if total:
        frac = min(1.0, done / total)
        fill = int(frac * width)
        sys.stdout.write(f"\r  [{'#' * fill}{'.' * (width - fill)}] {frac * 100:5.1f}% {done / 1e6:7.1f} MB")
    else:
        sys.stdout.write(f"\r  {done / 1e6:7.1f} MB")
    sys.stdout.flush()


def media_choice(target: Target, opts):
    media = "video" if opts.video else (target.media or "audio")
    if media == "video":
        quality = opts.video if opts.video in VIDEO_QUALITIES else (target.quality or "high")
    else:
        quality = "high" if opts.hq else (target.quality if target.quality in AUDIO_QUALITIES else
                                          config.load_config().get("quality", "low"))
    return media, quality


def run_target(target: Target, opts) -> int:
    root = os.path.abspath(os.path.expanduser(opts.out or config.download_dir()))
    media, quality = media_choice(target, opts)

    if target.kind == "search":
        picked = pick_from_search(target.value, opts)
        return run_target(picked, opts) if picked else 0

    if target.kind == "sermon":
        s = single_sermon(target.value)
        if media == "video" and s.has_video is False:
            print("This sermon has no video.")
            return 1
        print(f"{s.title or s.sermon_id}" + (f" — {s.speaker}" if s.speaker else ""))
        status, path = download_sermon(s, root, media, quality, progress=_bar if interactive_tty() else None)
        print()
        if status == "missing":
            print(f"No {media} file is available for {s.sermon_id}.")
            return 1
        print(f"{'Already have' if status == 'skipped' else 'Saved'}: {path}")
        return 0

    # Collections
    sort = "oldest" if opts.oldest else "newest"
    if target.kind == "speaker":
        sid = speaker_id_for_name(target.value, opts) if target.by_name else target.value
        if not sid:
            return 1
        print("Listing sermons…")
        sermons = speaker_sermons(sid, limit=opts.limit, sort=sort)
        name = sermons[0].speaker if sermons else f"speaker {sid}"
    elif target.kind == "broadcaster":
        print("Listing sermons…")
        sermons = broadcaster_sermons(target.value, limit=opts.limit, sort=sort)
        name = sermons[0].broadcaster if sermons else target.value
    elif target.kind == "series":
        print("Listing sermons…")
        name, sermons = series_sermons(target.value, limit=opts.limit)
    else:
        print(f"Unknown kind: {target.kind}")
        return 2

    if media == "video":
        sermons = [s for s in sermons if s.has_video is not False]
    if not sermons:
        print("No sermons found.")
        return 1

    have = sum(1 for s in sermons if existing_path(s, root, media, quality))
    todo = len(sermons) - have
    print(f"{name}: {len(sermons):,} sermons" + (f" ({have:,} already downloaded)" if have else ""))
    if opts.list:
        for s in sermons:
            print(f"  {s.date or '          '}  {s.title}  [{s.sermon_id}]")
        return 0
    if todo == 0:
        print("Nothing new to download.")
        return 0
    if not confirm(f"Download {todo:,} {media} files to {root}?", opts):
        return 0

    width = len(str(len(sermons)))

    def on_done(i, n, s, status, path):
        if status == "skipped":
            return
        mark = {"downloaded": "✓", "missing": "✗ no file:"}.get(status, f"✗ {status}:")
        try:
            shown = os.path.relpath(path, root)
        except ValueError:
            shown = str(path)
        print(f"[{i:>{width}}/{n}] {mark} {shown}")

    workers = opts.jobs or int(config.load_config().get("workers", 3))
    counts = download_many(sermons, root, media, quality, workers=workers, on_done=on_done)
    print(f"\nDone: {counts['downloaded']} downloaded, {counts['skipped']} already had"
          + (f", {counts['missing']} unavailable" if counts["missing"] else "")
          + (f", {counts['error']} failed" if counts["error"] else ""))
    return 1 if counts["error"] else 0


def interactive(opts) -> int:
    print(f"sapy {__version__} — SermonAudio downloader")
    print(f"Saving to: {os.path.abspath(opts.out or config.download_dir())}   (change with -o or `sapy config set download_dir DIR`)")
    while True:
        text = ask("\nSearch, or paste a link/ID (Enter to quit): ")
        if not text:
            return 0
        try:
            run_target(resolve(text), opts)
        except (ValueError, api.ApiError) as e:
            print(f"Error: {e}")
        except KeyboardInterrupt:
            print("\nStopped.")


# ---------- config ----------

def config_cmd(args: List[str]) -> int:
    if len(args) >= 3 and args[0] == "set":
        key, value = args[1], " ".join(args[2:])
        if key not in config.DEFAULTS:
            print(f"Unknown setting {key}. Settings: {', '.join(config.DEFAULTS)}")
            return 2
        if isinstance(config.DEFAULTS[key], bool):
            value = value.lower() in ("1", "true", "yes", "on")
        elif isinstance(config.DEFAULTS[key], int):
            value = int(value)
        elif key == "download_dir":
            value = os.path.abspath(os.path.expanduser(value))
        config.save_config(key, value)
        print(f"{key} saved.")
        return 0
    if len(args) == 2 and args[0] == "unset":
        config.save_config(args[1], config.DEFAULTS.get(args[1], ""))
        print(f"{args[1]} reset.")
        return 0
    print(f"Config file: {config.config_path()}")
    for k, v in config.load_config().items():
        if k == "api_key" and v:
            v = "…" + str(v)[-6:]
        print(f"  {k} = {v!r}")
    return 0


# ---------- entry point ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sapy", description="Search and download sermons from SermonAudio.com.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n", 2)[2])
    p.add_argument("words", nargs="*", help=argparse.SUPPRESS)
    p.add_argument("-o", "--out", help="download folder (default: config download_dir or current folder)")
    p.add_argument("--video", nargs="?", const="high", choices=VIDEO_QUALITIES,
                   help="download video instead of audio (default quality: high = 720p)")
    p.add_argument("--hq", action="store_true", help="high-quality audio (default is low, which is fine for speech)")
    p.add_argument("-n", "--limit", type=int,
                   help="only N sermons: the newest for a speaker/broadcaster, the first N of a series")
    p.add_argument("--oldest", action="store_true", help="with -n, take the oldest N instead")
    p.add_argument("--newest", action="store_true", help="sort search results by newest")
    p.add_argument("-j", "--jobs", type=int, help="parallel downloads (default 3)")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    p.add_argument("-l", "--list", action="store_true", help="list what would be downloaded and stop")
    p.add_argument("-V", "--version", action="version", version=f"sapy {__version__}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "config":
        return config_cmd(argv[1:])
    if argv and argv[0] == "gui":
        from .gui import run
        return run()

    opts = build_parser().parse_args(argv)
    words = opts.words
    try:
        if not words:
            return interactive(opts) if interactive_tty() else (build_parser().print_help() or 0)
        kind = None
        if words[0] in COMMANDS:
            kind, words = words[0], words[1:]
            if not words:
                print(f"sapy {kind}: needs a link, ID or name.")
                return 2
        text = " ".join(words)
        if kind == "search":
            show_search(text, "newest-published" if opts.newest else None)
            return 0
        return run_target(resolve(text, kind), opts)
    except (ValueError, api.ApiError) as e:
        print(f"Error: {e}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
