"""
sapy command line.

  sapy                          interactive: search or paste links, pick, download
  sapy <link | sermon ID>       download whatever the link points to
  sapy <words...>               search, then pick from the results
  sapy speaker "paul washer"    everything by a speaker (name or ID)
  sapy series 151324            a whole series (ID, link or name)
  sapy broadcaster heartcry     a whole church/broadcaster catalog
  sapy search <words...>        just show search results
  sapy config [set|unset KEY VALUE]

Quality:
  audio  -q high (default) | -q low                 (--small is short for -q low:
         about a sixth the size, but noticeably muffled)
  video  --video [360p|720p|1080p|best]             (default 720p; falls back
         to the nearest quality a sermon actually has)
  -F     show which formats a sermon has, with sizes
"""

import argparse
import os
import shutil
import sys
from typing import Callable, List, Optional, Tuple

from . import __version__, api, config
from .download import (AUDIO_QUALITIES, QUALITY_ALIASES, VIDEO_QUALITIES, Sermon, broadcaster_series, broadcaster_sermons,
                       download_many, download_sermon, existing_path, formats, group_by_series,
                       normalize_quality, series_sermons, single_sermon, speaker_sermons)
from .resolve import KINDS, Target, resolve

COMMANDS = ("search", "config") + KINDS

FORMAT_MENU = [
    ("audio", "high", "Audio", "full quality"),
    ("audio", "low", "Audio, small", "a sixth the size, muffled"),
    ("video", "360p", "Video 360p", ""),
    ("video", "720p", "Video 720p", ""),
    ("video", "1080p", "Video 1080p", ""),
    ("video", "best", "Video, best available", ""),
]


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


def fmt_label(media: str, quality: str) -> str:
    return next(lbl for m, q, lbl, _ in FORMAT_MENU if (m, q) == (media, quality))


def mb(n: Optional[int]) -> str:
    return f"{n / 1e6:,.0f} MB" if n and n >= 1e6 else f"{(n or 0) / 1e3:,.0f} KB"


# ---------- format selection ----------

def video_quality_or_blank(q: str) -> str:
    """Canonical spelling of a video quality the user typed (360p/720p/1080p/best), or ''."""
    q = (q or "").lower().strip()
    q = QUALITY_ALIASES.get(q, q)
    q = q + "p" if q.isdigit() else q
    return q if q in VIDEO_QUALITIES else ""


def media_choice(target: Target, opts) -> Tuple[str, str]:
    """(media, quality) from flags, then the link itself, then settings."""
    cfg = config.load_config()
    q = (opts.quality or "").lower()
    q_is_video = q not in AUDIO_QUALITIES and bool(video_quality_or_blank(q))
    if opts.video is not None or target.media == "video" or q_is_video:
        from_link = target.quality if target.media == "video" else ""
        return "video", normalize_quality("video", opts.video or q or from_link or cfg.get("video_quality"))
    from_link = target.quality if target.media == "audio" else ""
    wanted = "low" if opts.small else (q or from_link or cfg.get("quality"))
    return "audio", normalize_quality("audio", wanted)


def choose_format(current: Tuple[str, str], sizes: Optional[dict] = None,
                  has_video: Optional[bool] = None) -> Tuple[str, str]:
    """Numbered format menu. With sizes (from download.formats), only real options are shown."""
    options = []
    for media, q, label, note in FORMAT_MENU:
        if media == "video" and has_video is False:
            continue
        if sizes is not None:
            if q == "best":
                avail = [vq for vq in ("1080p", "720p", "360p") if sizes.get(("video", vq))]
                if not avail:
                    continue
                note = f"→ {avail[0]}"
            else:
                size = sizes.get((media, q))
                if not size:
                    continue
                note = mb(size)
        options.append((media, q, label, note))
    if not options:
        return current
    print()
    for i, (media, q, label, note) in enumerate(options, 1):
        star = "*" if (media, q) == current else " "
        print(f" {star}{i}  {label}" + (f"  ({note})" if note else ""))
    choice = ask("Format (Enter = *): ")
    if choice.isdigit() and 1 <= int(choice) <= len(options):
        media, q, _, _ = options[int(choice) - 1]
        return media, q
    return current


# ---------- search ----------

def search_entries(query: str, sort_by: Optional[str] = None) -> List[tuple]:
    """[(group, label, Target)] across speakers, series, broadcasters, sermons."""
    data = api.search(query, sort_by=sort_by)
    entries = []
    for s in (data.get("speakerResults") or [])[:5]:
        n = s.get("sermonCount")
        entries.append(("Speakers", f"{s.get('displayName')}" + (f" · {n:,} sermons" if isinstance(n, int) else ""),
                        Target("speaker", str(s.get("speakerID")))))
    for s in (data.get("seriesResults") or [])[:6]:
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
        video = " ▶" if s.get("hasVideo") else ""
        entries.append(("Sermons", f"{s.get('fullTitle') or s.get('displayTitle')} — {sp}, {date}{video}",
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


def _pick_by_name(name: str, results: List[dict], id_key: str, count_key: str,
                  describe: Callable[[dict], str], opts, what: str) -> Optional[str]:
    """Exact name matches win; ties are asked about in a terminal, else the biggest is taken."""
    if not results:
        print(f"No {what} found for “{name}”.")
        return None
    exact = [r for r in results if (r.get("displayName") or r.get("title") or "").lower() == name.lower()]
    candidates = (exact or results)[:8]
    if len(candidates) == 1 or not interactive_tty() or opts.yes:
        return str(max(candidates, key=lambda r: r.get(count_key) or 0).get(id_key))
    print(f"{what.capitalize()} matching “{name}”:")
    for i, r in enumerate(candidates, 1):
        print(f"  {i}  {describe(r)}")
    choice = ask("Which one? ")
    if choice.isdigit() and 1 <= int(choice) <= len(candidates):
        return str(candidates[int(choice) - 1].get(id_key))
    return None


def speaker_id_for_name(name: str, opts) -> Optional[str]:
    return _pick_by_name(name, api.search(name).get("speakerResults") or [], "speakerID", "sermonCount",
                         lambda s: f"{s.get('displayName')} · {s.get('sermonCount', '?')} sermons", opts, "speaker")


def series_id_for_name(name: str, opts) -> Optional[str]:
    return _pick_by_name(
        name, api.search(name).get("seriesResults") or [], "seriesID", "count",
        lambda s: f"{s.get('title')} · {(s.get('broadcaster') or {}).get('displayName', '')} · {s.get('count', '?')} sermons",
        opts, "series")


# ---------- downloading ----------

class Bar:
    def __init__(self):
        self.last = -1

    def __call__(self, done: int, total: int) -> None:
        pct = int(done * 1000 / total) if total else done // 1_000_000
        if pct == self.last:
            return
        self.last = pct
        width = max(10, min(40, shutil.get_terminal_size().columns - 34))
        if total:
            frac = min(1.0, done / total)
            fill = int(frac * width)
            sys.stdout.write(f"\r  [{'#' * fill}{'.' * (width - fill)}] {frac * 100:5.1f}%  {mb(done)} / {mb(total)} ")
        else:
            sys.stdout.write(f"\r  {mb(done)} ")
        sys.stdout.flush()


def show_formats(sermon_id: str) -> int:
    s = single_sermon(sermon_id)
    print(f"{s.title or s.sermon_id}" + (f" — {s.speaker}" if s.speaker else ""))
    sizes = formats(sermon_id, video=s.has_video is not False)
    for (media, q), size in sizes.items():
        print(f"  {fmt_label(media, q):<12} {'-q ' + q if media == 'audio' else '--video ' + q:<16} "
              f"{mb(size) if size else 'not available'}")
    return 0


def run_sermon(sermon_id: str, root: str, media: str, quality: str, opts, ask_format: bool) -> int:
    s = single_sermon(sermon_id)
    print(f"{s.title or s.sermon_id}" + (f" — {s.speaker}" if s.speaker else "")
          + (f", {s.date}" if s.date else ""))
    if ask_format and interactive_tty() and not opts.yes:
        media, quality = choose_format((media, quality), formats(sermon_id, video=s.has_video is not False),
                                       s.has_video)
    if media == "video" and s.has_video is False:
        print("This sermon has no video.")
        return 1
    status, path = download_sermon(s, root, media, quality, progress=Bar() if interactive_tty() else None)
    if interactive_tty() and status == "downloaded":
        print()
    if status == "missing":
        print(f"No {media} file is available for this sermon.")
        return 1
    if media == "video" and status == "downloaded" and f"[{quality}]" not in path.name and quality != "best":
        print(f"({quality} isn't available for this sermon; got the nearest quality instead.)")
    print(f"{'Already have' if status == 'skipped' else 'Saved'}: {path}")
    return 0


def run_download(sermons: List[Sermon], name: str, root: str, media: str, quality: str, opts) -> int:
    if media == "video":
        no_video = sum(1 for s in sermons if s.has_video is False)
        sermons = [s for s in sermons if s.has_video is not False]
        if no_video:
            print(f"({no_video} of these have no video and are left out.)")
    if not sermons:
        print("Nothing to download.")
        return 1
    have = sum(1 for s in sermons if existing_path(s, root, media, quality))
    todo = len(sermons) - have
    if opts.list:
        for s in sermons:
            num = f"{s.track:>3}. " if s.track else ""
            print(f"  {num}{s.date or '          '}  {s.title}  [{s.sermon_id}]")
        print(f"{len(sermons):,} sermons" + (f", {have:,} already downloaded" if have else ""))
        return 0
    if todo == 0:
        print(f"All {len(sermons):,} already downloaded.")
        return 0
    if not confirm(f"Download {todo:,} sermons ({fmt_label(media, quality)}) to {root}?", opts):
        return 0

    width = len(str(len(sermons)))

    def on_done(i, n, s, status, path):
        if status == "skipped":
            return
        mark = {"downloaded": "✓", "missing": "✗ not available:"}.get(status, f"✗ {status}:")
        try:
            shown = os.path.relpath(path, root)
        except ValueError:
            shown = str(path)
        print(f"[{i:>{width}}/{n}] {mark} {shown}")

    workers = opts.jobs or int(config.load_config().get("workers", 3))
    try:
        counts = download_many(sermons, root, media, quality, workers=workers, on_done=on_done)
    except KeyboardInterrupt:
        print("\nStopped. Run the same command again to pick up where it left off.")
        return 130
    print(f"\nDone: {counts['downloaded']} downloaded, {counts['skipped']} already had"
          + (f", {counts['missing']} not available in that format" if counts["missing"] else "")
          + (f", {counts['error']} failed (run again to retry)" if counts["error"] else ""))
    return 1 if counts["error"] else 0


def pick_series(entries: List[Tuple[str, int, str]]) -> Optional[str]:
    """entries: [(title, count, series_id)]. Number picks; text filters. Returns a series ID."""
    shown = entries
    while True:
        print()
        for i, (title, count, _sid) in enumerate(shown[:40], 1):
            print(f"  {i:>2}  {title or '(not in a series)'} · {count} sermon{'s' if count != 1 else ''}")
        if len(shown) > 40:
            print(f"  … {len(shown) - 40} more; type part of a name to narrow it down")
        choice = ask("Series number, or text to filter (Enter to go back): ")
        if not choice:
            return None
        if choice.isdigit() and 1 <= int(choice) <= min(40, len(shown)):
            return shown[int(choice) - 1][2]
        shown = [e for e in entries if choice.lower() in e[0].lower()] or entries
        if shown is entries:
            print("  No series match that.")


def run_collection(target: Target, root: str, media: str, quality: str, opts) -> int:
    sort = "oldest" if opts.oldest else "newest"
    series_list: List[Tuple[str, int, str]] = []
    load: Callable[[Optional[int]], List[Sermon]]

    if target.kind == "series":
        sid = series_id_for_name(target.value, opts) if target.by_name else target.value
        if not sid:
            return 1
        print("Listing sermons…")
        name, all_sermons = series_sermons(sid)
        total = len(all_sermons)
        load = lambda n: (all_sermons[-n:] if opts.newest else all_sermons[:n]) if n else all_sermons
        summary = f"{name}: {total:,} sermons"
    elif target.kind == "speaker":
        sid = speaker_id_for_name(target.value, opts) if target.by_name else target.value
        if not sid:
            return 1
        print("Listing sermons…")
        all_sermons = speaker_sermons(sid, sort=sort)
        if not all_sermons:
            print("No sermons found.")
            return 1
        name, total = all_sermons[0].speaker, len(all_sermons)
        groups = group_by_series(all_sermons)
        series_list = [(t, len(g), sid) for t, g in groups if t
                       for sid in [next((s.series_id for s in g if s.series_id), "")] if sid]
        loose = next((g for t, g in groups if not t), [])
        load = lambda n: all_sermons[:n] if n else all_sermons
        summary = f"{name}: {total:,} sermons in {len(series_list)} series" + (
            f" (+{len(loose)} standalone)" if loose else "")
    else:  # broadcaster
        print("Looking up broadcaster…")
        total = api.count_sermons(broadcasterID=target.value)
        first = broadcaster_sermons(target.value, limit=1)
        if not first:
            print("No sermons found.")
            return 1
        name = first[0].broadcaster or target.value
        series_list = [(d["title"], d["count"], d["seriesID"]) for d in broadcaster_series(target.value)]
        cache = {}

        def load(n):
            if n not in cache:
                print("Listing sermons…")
                cache[n] = broadcaster_sermons(target.value, limit=n, sort=sort)
            return cache[n]
        summary = f"{name}: {total:,} sermons in {len(series_list)} series"

    print(summary)

    # Flags decide when not interactive
    if not interactive_tty() or opts.yes or opts.list:
        if opts.series_pick and series_list:
            for title, count, sid in series_list:
                print(f"  {sid:>8}  {title} · {count} sermons")
            print("Download one with: sapy series <ID>")
            return 0
        return run_download(load(opts.limit), name, root, media, quality, opts)

    if opts.series_pick and series_list:
        picked = pick_series(series_list)
        return run_collection(Target("series", picked), root, media, quality, opts) if picked else 0

    while True:
        actions = [f"[Enter] all {total:,}"]
        if series_list:
            actions.append("[s] pick a series")
        actions += ["[n 20] newest 20" if target.kind != "series" else "[n 20] first 20",
                    "[f] format", "[l] list", "[q] back"]
        print(f"\nFormat: {fmt_label(media, quality)}")
        choice = ask("  ".join(actions) + "\n> ").lower()
        if choice in ("q", "quit", "back"):
            return 0
        if choice == "f":
            media, quality = choose_format((media, quality))
            continue
        if choice == "s" and series_list:
            picked = pick_series(series_list)
            if picked:
                return run_collection(Target("series", picked), root, media, quality, opts)
            continue
        if choice == "l":
            opts.list = True
            run_download(load(None), name, root, media, quality, opts)
            opts.list = False
            continue
        n = None
        if choice.startswith("n"):
            digits = choice[1:].strip()
            if not digits.isdigit():
                print("  Type n and a number, like: n 20")
                continue
            n = int(digits)
        elif choice:
            continue
        opts.yes = True  # this menu was the confirmation
        try:
            return run_download(load(n), name, root, media, quality, opts)
        finally:
            opts.yes = False


def run_target(target: Target, opts, ask_format: bool = False) -> int:
    root = os.path.abspath(os.path.expanduser(opts.out or config.download_dir()))
    media, quality = media_choice(target, opts)

    if target.kind == "search":
        picked = pick_from_search(target.value, opts)
        return run_target(picked, opts, ask_format=True) if picked else 0
    if target.kind == "sermon":
        if opts.formats:
            return show_formats(target.value)
        return run_sermon(target.value, root, media, quality, opts, ask_format)
    if target.kind in ("speaker", "series", "broadcaster"):
        return run_collection(target, root, media, quality, opts)
    print(f"Unknown kind: {target.kind}")
    return 2


def interactive(opts) -> int:
    print(f"sapy {__version__} — SermonAudio downloader")
    print(f"Saving to: {os.path.abspath(opts.out or config.download_dir())}"
          "   (change with -o or `sapy config set download_dir DIR`)")
    while True:
        text = ask("\nSearch, or paste a link/ID (Enter to quit): ")
        if not text:
            return 0
        try:
            run_target(resolve(text), opts, ask_format=True)
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
        elif key == "quality":
            value = normalize_quality("audio", value)
        elif key == "video_quality":
            value = normalize_quality("video", value)
        config.save_config(key, value)
        shown = "…" + str(value)[-6:] if key == "api_key" else repr(value)
        print(f"{key} = {shown}")
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
    p.add_argument("--video", nargs="?", const="", metavar="QUALITY",
                   help="video instead of audio: 360p, 720p, 1080p or best (default: video_quality setting, 720p)")
    p.add_argument("-q", "--quality", help="audio: low or high; video: 360p, 720p, 1080p or best")
    p.add_argument("--small", action="store_true", help="small, low-quality audio (same as -q low)")
    p.add_argument("-F", "--formats", action="store_true", help="show the formats a sermon has, with sizes")
    p.add_argument("-s", "--series", dest="series_pick", action="store_true",
                   help="for a speaker or broadcaster: pick one of their series (lists them with -l / -y)")
    p.add_argument("-n", "--limit", type=int,
                   help="only N sermons: the newest for a speaker/broadcaster, the first N of a series")
    p.add_argument("--oldest", action="store_true", help="with -n on a speaker/broadcaster, take the oldest N")
    p.add_argument("--newest", action="store_true", help="newest first in search; with -n on a series, the latest N")
    p.add_argument("-j", "--jobs", type=int, help="parallel downloads (default: workers setting, 3)")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask, just download")
    p.add_argument("-l", "--list", action="store_true", help="list what would be downloaded and stop")
    p.add_argument("-V", "--version", action="version", version=f"sapy {__version__}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # Piped output on Windows is cp1252, which can't encode ✓ ▶ → and would crash print()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    if argv and argv[0] == "config":
        return config_cmd(argv[1:])

    parser = build_parser()
    opts = parser.parse_args(argv)
    if opts.video and not video_quality_or_blank(opts.video):
        parser.error(f"--video: unknown quality {opts.video!r} (use 360p, 720p, 1080p or best)")
    if opts.quality and opts.quality.lower() not in AUDIO_QUALITIES and not video_quality_or_blank(opts.quality):
        parser.error(f"-q: unknown quality {opts.quality!r} (audio: low, high; video: 360p, 720p, 1080p, best)")
    words = opts.words
    try:
        if not words:
            if interactive_tty():
                return interactive(opts)
            parser.print_help()
            return 0
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
