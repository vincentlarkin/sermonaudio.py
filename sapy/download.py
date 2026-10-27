"""
Turn targets into sermon lists and download them.

Layout:  <root>/<Speaker>/<Series>/<YYYY-MM-DD> - <Title>.mp3
         (sermons outside a series sit directly in the speaker folder)

Existing files are skipped, partial downloads resume from their .part file,
and audio/video gets title/artist/album/date tags from SermonAudio's metadata.
"""

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

from . import api

CLOUD = "https://cloud.sermonaudio.com/media"
AUDIO_QUALITIES = ("low", "high")
VIDEO_QUALITIES = ("low", "high", "1080p")


@dataclass
class Sermon:
    sermon_id: str
    title: str = ""
    speaker: str = ""
    broadcaster: str = ""
    series: str = ""
    date: str = ""
    has_video: Optional[bool] = None

    @classmethod
    def from_api(cls, d: dict) -> "Sermon":
        def name(obj, *keys):
            if isinstance(obj, dict):
                for k in keys:
                    if obj.get(k):
                        return str(obj[k])
            return str(obj) if isinstance(obj, str) else ""

        return cls(
            sermon_id=str(d.get("sermonID")),
            title=d.get("fullTitle") or d.get("displayTitle") or "",
            speaker=name(d.get("speaker"), "displayName", "speakerName"),
            broadcaster=name(d.get("broadcaster"), "displayName", "shortName"),
            series=name(d.get("series"), "title", "displayTitle") or (d.get("subtitle") or ""),
            date=str(d.get("preachDate") or "")[:10],
            has_video=d.get("hasVideo"),
        )

    @classmethod
    def from_feed(cls, d: dict, series_title: str) -> "Sermon":
        return cls(sermon_id=d["sermonID"], title=d["title"], speaker=d["speaker"],
                   series=series_title, date=d["date"])

    @property
    def url(self) -> str:
        return f"https://www.sermonaudio.com/sermons/{self.sermon_id}"


# ---------- filenames ----------

def clean(name: str, fallback: str = "Untitled") -> str:
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', " ", name or "")
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    return name[:150].strip() or fallback


def target_path(s: Sermon, root: str, media: str = "audio", quality: str = "low") -> Path:
    folder = Path(root) / clean(s.speaker or s.broadcaster, "Unknown Speaker")
    if s.series:
        folder /= clean(s.series)
    stem = clean(s.title, s.sermon_id)
    if s.date:
        stem = f"{s.date} - {stem}"
    if media == "video":
        return folder / f"{stem} [{'360p' if quality == 'low' else '720p' if quality == 'high' else quality}].mp4"
    return folder / f"{stem}.mp3"


def existing_path(s: Sermon, root: str, media: str = "audio", quality: str = "low") -> Optional[Path]:
    p = target_path(s, root, media, quality)
    return p if p.exists() else None


# ---------- fetching one file ----------

def _media_urls(sermon_id: str, media: str, quality: str) -> List[Tuple[str, str]]:
    if media == "video":
        return [(quality, f"{CLOUD}/video/{quality}/{sermon_id}.mp4")]
    order = [quality] + [q for q in AUDIO_QUALITIES if q != quality]
    return [(q, f"{CLOUD}/audio/{q}/{sermon_id}.mp3") for q in order]


def _fetch(url: str, dest: Path, progress: Optional[Callable[[int, int], None]] = None) -> bool:
    """Download url to dest via dest.part, resuming if a .part exists. False on 404/403."""
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with api.session.get(url, headers=headers, stream=True, timeout=60) as r:
        if r.status_code in (403, 404, 410):
            return False
        if r.status_code == 416:  # .part already complete
            os.replace(part, dest)
            return True
        r.raise_for_status()
        if "text/html" in r.headers.get("Content-Type", ""):
            return False
        if r.status_code != 206:
            have = 0
        total = have + int(r.headers.get("Content-Length") or 0)
        done = have
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(part, "ab" if have else "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    os.replace(part, dest)
    return True


def tag(path: Path, s: Sermon) -> None:
    try:
        from mutagen import File as MutagenFile
        f = MutagenFile(path, easy=True)
        if f is None:
            return
        if f.tags is None:
            f.add_tags()
        fields = {"title": s.title, "artist": s.speaker, "album": s.series or s.broadcaster,
                  "albumartist": s.broadcaster or s.speaker, "date": s.date, "genre": "Sermon"}
        for k, v in fields.items():
            if v:
                try:
                    f[k] = v
                except (KeyError, ValueError):
                    pass
        f.save()
    except Exception as e:  # tags are a nicety; never fail a download over them
        print(f"[tag] {path.name}: {e}")


def download_sermon(s: Sermon, root: str, media: str = "audio", quality: str = "low",
                    progress: Optional[Callable[[int, int], None]] = None) -> Tuple[str, Path]:
    """Returns ("skipped" | "downloaded" | "missing", path)."""
    dest = target_path(s, root, media, quality)
    if dest.exists():
        return "skipped", dest
    for _q, url in _media_urls(s.sermon_id, media, quality):
        if _fetch(url, dest, progress):
            tag(dest, s)
            return "downloaded", dest
    return "missing", dest


def download_many(sermons: Iterable[Sermon], root: str, media: str = "audio", quality: str = "low",
                  workers: int = 3,
                  on_done: Optional[Callable[[int, int, Sermon, str, Path], None]] = None,
                  stop: Optional[threading.Event] = None) -> dict:
    """Download in parallel. on_done(index, total, sermon, status, path); status may be "error: ..."."""
    items = list(sermons)
    counts = {"downloaded": 0, "skipped": 0, "missing": 0, "error": 0}
    lock = threading.Lock()
    finished = 0

    def job(s):
        if stop is not None and stop.is_set():
            return s, "cancelled", target_path(s, root, media, quality)
        try:
            status, path = download_sermon(s, root, media, quality)
        except Exception as e:
            return s, f"error: {e}", target_path(s, root, media, quality)
        return s, status, path

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for fut in as_completed([pool.submit(job, s) for s in items]):
            s, status, path = fut.result()
            with lock:
                finished += 1
                key = "error" if status.startswith("error") else status
                counts[key] = counts.get(key, 0) + 1
                if on_done:
                    on_done(finished, len(items), s, status, path)
    return counts


# ---------- collecting sermon lists ----------

def _matching(items: Iterable[dict], field: str, id_key: str, want: str) -> Iterable[dict]:
    """
    Guard against the API ignoring an unknown filter and returning everything:
    stop if the first result doesn't belong to what we asked for.
    """
    first = True
    for d in items:
        got = str((d.get(field) or {}).get(id_key, "")) if isinstance(d.get(field), dict) else ""
        if first and got and got.lower() != str(want).lower():
            raise api.ApiError(f"API filter on {field} was ignored (got {got}, wanted {want}).")
        first = False
        yield d


def speaker_sermons(speaker_id: str, limit: Optional[int] = None, sort: str = "newest") -> List[Sermon]:
    it = api.sermons(speakerID=speaker_id, sortBy=sort, limit=limit)
    return [Sermon.from_api(d) for d in _matching(it, "speaker", "speakerID", speaker_id)]


def broadcaster_sermons(broadcaster_id: str, limit: Optional[int] = None, sort: str = "newest") -> List[Sermon]:
    it = api.sermons(broadcasterID=broadcaster_id, sortBy=sort, limit=limit)
    return [Sermon.from_api(d) for d in _matching(it, "broadcaster", "broadcasterID", broadcaster_id)]


def series_sermons(series_id: str, limit: Optional[int] = None) -> Tuple[str, List[Sermon]]:
    """(series title, sermons oldest-first). API first; RSS feed (max 100) as fallback."""
    try:
        it = api.sermons(series=series_id, sortBy="oldest", limit=limit)
        found = [Sermon.from_api(d) for d in _matching(it, "series", "seriesID", series_id)]
        if found:
            return found[0].series or f"Series {series_id}", found
    except api.ApiError as e:
        print(f"[series] API listing unavailable ({e}); using the RSS feed.")
    feed = api.series_feed(series_id)
    if len(feed["items"]) >= api.FEED_CAP:
        print(f"[series] Note: the RSS feed only lists the newest {api.FEED_CAP} sermons of this series.")
    items = feed["items"][:limit] if limit else feed["items"]
    try:  # upgrade to full metadata when the API is reachable
        full = {s.sermon_id: s for s in map(Sermon.from_api, api.sermons_by_ids([i["sermonID"] for i in items]))}
    except Exception:
        full = {}
    return feed["title"], [full.get(i["sermonID"]) or Sermon.from_feed(i, feed["title"]) for i in items]


def single_sermon(sermon_id: str) -> Sermon:
    try:
        d = api.sermon(sermon_id)
        if d:
            return Sermon.from_api(d)
    except Exception as e:
        print(f"[sermon] Metadata lookup failed ({e}); saving with the ID as its name.")
    return Sermon(sermon_id=sermon_id)
