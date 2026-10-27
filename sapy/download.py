"""
Turn targets into sermon lists and download them.

Layout:  <root>/<Speaker>/<Series>/<YYYY-MM-DD> - <Title>.mp3
         (sermons outside a series sit directly in the speaker folder;
          a series with several speakers goes under its broadcaster instead)

Existing files are skipped, partial downloads resume from their .part file,
and files get title/artist/album/date/track tags from SermonAudio's metadata.
"""

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import api

CLOUD = "https://cloud.sermonaudio.com/media"

AUDIO_QUALITIES = ("high", "low")  # low is ~16 kbps: a sixth the size, but muffled
# Label -> SermonAudio's tier name. Tiers really are 360p/720p/1080p for modern uploads.
VIDEO_TIERS = {"360p": "low", "720p": "high", "1080p": "1080p"}
VIDEO_QUALITIES = ("360p", "720p", "1080p", "best")
QUALITY_ALIASES = {"low": "360p", "high": "720p", "hd": "720p", "fullhd": "1080p", "max": "best"}

# When a tier doesn't exist the CDN answers 200 with this stock "unavailable" clip.
PLACEHOLDER_SIZE = 2274516
PLACEHOLDER_ETAG = "c0e34c3701be4ef90947afcebefab6d3"

_cancel = threading.Event()


class Cancelled(Exception):
    pass


def normalize_quality(media: str, quality: Optional[str]) -> str:
    q = (quality or "").lower().strip()
    if media == "video":
        q = QUALITY_ALIASES.get(q, q)
        if q.isdigit():
            q += "p"
        return q if q in VIDEO_QUALITIES else "720p"
    return q if q in AUDIO_QUALITIES else "high"


def quality_chain(media: str, quality: str) -> List[str]:
    """Qualities to try, in order: the one asked for, then lower, then higher."""
    if media == "audio":
        return [quality] + [q for q in AUDIO_QUALITIES if q != quality]
    order = ["360p", "720p", "1080p"]
    if quality == "best":
        return order[::-1]
    i = order.index(quality)
    return [quality] + order[:i][::-1] + order[i + 1:]


@dataclass
class Sermon:
    sermon_id: str
    title: str = ""
    speaker: str = ""
    broadcaster: str = ""
    series: str = ""
    date: str = ""
    has_video: Optional[bool] = None
    series_id: str = ""
    folder: str = ""            # overrides the top-level folder (speaker) when set
    track: int = 0
    track_total: int = 0

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
            series_id=str((d.get("series") or {}).get("seriesID") or "") if isinstance(d.get("series"), dict) else "",
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


def target_path(s: Sermon, root: str, media: str = "audio", quality: str = "high") -> Path:
    folder = Path(root) / clean(s.folder or s.speaker or s.broadcaster, "Unknown Speaker")
    if s.series:
        folder /= clean(s.series)
    stem = clean(s.title, s.sermon_id)
    if s.date:
        stem = f"{s.date} - {stem}"
    if media == "video":
        return folder / f"{stem} [{quality}].mp4"
    return folder / f"{stem}.mp3"


def existing_path(s: Sermon, root: str, media: str = "audio", quality: str = "high") -> Optional[Path]:
    """A file already on disk for this sermon (in the asked-for quality or a fallback of it)."""
    chain = quality_chain(media, quality) if media == "video" else [quality]
    for q in chain:
        p = target_path(s, root, media, q)
        if p.exists():
            return p
    return None


# ---------- fetching one file ----------

def media_url(sermon_id: str, media: str, quality: str) -> str:
    if media == "video":
        return f"{CLOUD}/video/{VIDEO_TIERS[quality]}/{sermon_id}.mp4?download=true"
    return f"{CLOUD}/audio/{quality}/{sermon_id}.mp3?download=true"


def _is_placeholder(headers, total: int) -> bool:
    return headers.get("ETag", "").strip('W/"') == PLACEHOLDER_ETAG or total == PLACEHOLDER_SIZE


def _total_size(r, have: int) -> int:
    m = re.search(r"/(\d+)$", r.headers.get("Content-Range", ""))
    if m:
        return int(m.group(1))
    return have + int(r.headers.get("Content-Length") or 0)


def probe(sermon_id: str, media: str, quality: str) -> Optional[int]:
    """Size in bytes if this quality exists, else None. Costs one tiny request."""
    try:
        with api.session.get(media_url(sermon_id, media, quality), headers={"Range": "bytes=0-0"},
                             stream=True, timeout=30) as r:
            if r.status_code not in (200, 206):
                return None
            ctype = r.headers.get("Content-Type", "")
            if "html" in ctype or "mpegurl" in ctype:
                return None
            total = _total_size(r, 0)
            return None if _is_placeholder(r.headers, total) else total
    except Exception:
        return None


def formats(sermon_id: str, video: bool = True) -> Dict[Tuple[str, str], Optional[int]]:
    """{(media, quality): size or None} for every audio and (optionally) video quality."""
    wanted = [("audio", q) for q in AUDIO_QUALITIES]
    if video:
        wanted += [("video", q) for q in VIDEO_TIERS]
    with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
        sizes = pool.map(lambda mq: probe(sermon_id, *mq), wanted)
    return dict(zip(wanted, sizes))


def _fetch(url: str, dest: Path, progress: Optional[Callable[[int, int], None]] = None) -> bool:
    """Download url to dest via dest.part, resuming if a .part exists. False if unavailable."""
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with api.session.get(url, headers=headers, stream=True, timeout=60) as r:
        if r.status_code in (403, 404, 410, 422):
            return False
        if r.status_code == 416:  # .part already complete
            os.replace(part, dest)
            return True
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if "html" in ctype or "mpegurl" in ctype:
            return False
        if r.status_code != 206:
            have = 0
        total = _total_size(r, have)
        if _is_placeholder(r.headers, total):
            return False
        done = have
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(part, "ab" if have else "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                if _cancel.is_set():
                    raise Cancelled()  # .part stays for resuming next time
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    if total and part.stat().st_size < total:
        raise IOError(f"connection dropped at {part.stat().st_size} of {total} bytes; run again to resume")
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
                  "albumartist": s.folder or s.broadcaster or s.speaker, "date": s.date, "genre": "Sermon"}
        if s.track:
            fields["tracknumber"] = f"{s.track}/{s.track_total}" if s.track_total else str(s.track)
        for k, v in fields.items():
            if v:
                try:
                    f[k] = v
                except (KeyError, ValueError):
                    pass
        f.save()
    except Exception as e:  # tags are a nicety; never fail a download over them
        print(f"[tag] {path.name}: {e}")


def download_sermon(s: Sermon, root: str, media: str = "audio", quality: str = "high",
                    progress: Optional[Callable[[int, int], None]] = None) -> Tuple[str, Path]:
    """Returns (status, path); status is "downloaded", "skipped" or "missing".
    Falls back to the nearest available quality when the asked-for one doesn't exist."""
    quality = normalize_quality(media, quality)
    have = existing_path(s, root, media, quality)
    if have:
        return "skipped", have
    chain = quality_chain(media, quality)
    for q in chain:
        dest = target_path(s, root, media, q)
        if _fetch(media_url(s.sermon_id, media, q), dest, progress):
            tag(dest, s)
            return "downloaded", dest
    return "missing", target_path(s, root, media, chain[0])


def download_many(sermons: Iterable[Sermon], root: str, media: str = "audio", quality: str = "high",
                  workers: int = 3,
                  on_done: Optional[Callable[[int, int, Sermon, str, Path], None]] = None) -> dict:
    """Download in parallel. on_done(index, total, sermon, status, path); status may be "error: ...".
    Ctrl+C stops cleanly: running files keep their .part for next time."""
    items = list(sermons)
    counts = {"downloaded": 0, "skipped": 0, "missing": 0, "error": 0}
    _cancel.clear()

    def job(s):
        if _cancel.is_set():
            return s, "cancelled", None
        try:
            status, path = download_sermon(s, root, media, quality)
        except Cancelled:
            return s, "cancelled", None
        except Exception as e:
            return s, f"error: {e}", target_path(s, root, media, normalize_quality(media, quality))
        return s, status, path

    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        finished = 0
        for fut in as_completed([pool.submit(job, s) for s in items]):
            s, status, path = fut.result()
            if status == "cancelled":
                continue
            finished += 1
            counts["error" if status.startswith("error") else status] += 1
            if on_done:
                on_done(finished, len(items), s, status, path)
    except KeyboardInterrupt:
        _cancel.set()
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    finally:
        pool.shutdown(wait=True)
    return counts


# ---------- collecting sermon lists ----------

def _matching(items: Iterable[dict], field: str, id_key: str, want: str) -> Iterable[dict]:
    """
    Guard against the API ignoring a filter it doesn't know and returning the
    whole site: stop if the first result doesn't belong to what we asked for.
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


def group_by_series(sermons: List[Sermon]) -> List[Tuple[str, List[Sermon]]]:
    """[(series title, sermons)], biggest series first; sermons with no series last."""
    groups: Dict[str, List[Sermon]] = {}
    for s in sermons:
        groups.setdefault(s.series, []).append(s)
    named = sorted(((k, v) for k, v in groups.items() if k), key=lambda kv: (-len(kv[1]), kv[0].lower()))
    return named + ([("", groups[""])] if "" in groups else [])


def _number_series(title: str, sermons: List[Sermon], offset: int = 0, total: int = 0) -> List[Sermon]:
    """Oldest first, numbered as tracks; one folder for the whole series even with guest speakers."""
    speakers = {s.speaker for s in sermons if s.speaker}
    shared = None
    if len(speakers) > 1:
        shared = next((s.broadcaster for s in sermons if s.broadcaster), "") or "Various Speakers"
    total = total or len(sermons)
    for i, s in enumerate(sermons, 1):
        s.series = s.series or title
        s.track, s.track_total = offset + i, total
        if shared:
            s.folder = shared
    return sermons


def series_sermons(series_id: str, limit: Optional[int] = None, newest: bool = False) -> Tuple[str, List[Sermon]]:
    """(series title, sermons in order, numbered). API first; RSS feed (newest 100) as fallback."""
    info = {}
    try:
        info = api.series(series_id)
    except api.ApiError:
        pass
    title = info.get("title") or ""
    expected = info.get("count") or 0

    try:
        it = api.sermons(series=series_id, sortBy="oldest")
        found = [Sermon.from_api(d) for d in _matching(it, "series", "seriesID", series_id)]
        if found:
            found.sort(key=lambda s: (s.date or "9999", s.sermon_id))
            if expected and len(found) < expected:
                print(f"[series] Note: SermonAudio lists {expected} sermons but only {len(found)} are public.")
            title = title or found[0].series or f"Series {series_id}"
            _number_series(title, found)
            if limit:
                found = found[-limit:] if newest else found[:limit]
            return title, found
    except api.ApiError as e:
        print(f"[series] API listing unavailable ({e}); using the RSS feed.")

    feed = api.series_feed(series_id)
    title = title or feed["title"]
    items = feed["items"]
    if len(items) >= api.FEED_CAP:
        print(f"[series] Note: the RSS feed only lists the newest {api.FEED_CAP} sermons of this series.")
    try:  # upgrade to full metadata when the API is reachable
        full = {s.sermon_id: s for s in map(Sermon.from_api, api.sermons_by_ids([i["sermonID"] for i in items]))}
    except Exception:
        full = {}
    sermons = [full.get(i["sermonID"]) or Sermon.from_feed(i, title) for i in items]
    offset = max(0, expected - len(sermons)) if expected else 0
    _number_series(title, sermons, offset=offset, total=expected or len(sermons))
    if limit:
        sermons = sermons[-limit:] if newest else sermons[:limit]
    return title, sermons


def broadcaster_series(broadcaster_id: str) -> List[dict]:
    """[{seriesID, title, count}] for a broadcaster, biggest first."""
    out = [{"seriesID": str(d.get("seriesID")), "title": d.get("title") or "", "count": d.get("count") or 0}
           for d in api.broadcaster_series(broadcaster_id)]
    return sorted((d for d in out if d["count"]), key=lambda d: (-d["count"], d["title"].lower()))


def single_sermon(sermon_id: str) -> Sermon:
    try:
        d = api.sermon(sermon_id)
        if d:
            return Sermon.from_api(d)
    except Exception as e:
        print(f"[sermon] Metadata lookup failed ({e}); saving with the ID as its name.")
    return Sermon(sermon_id=sermon_id)
