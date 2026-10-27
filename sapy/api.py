"""
Thin client for the SermonAudio v2 "node" API, the same one sermonaudio.com uses.

API key, in order of preference:
  1. $SAPY_API_KEY
  2. "api_key" in the config file (your own key)
  3. the site's public key, cached in ~/.config/sapy/site_key.txt and
     re-read from the sermonaudio.com homepage whenever it stops working
"""

import json
import os
import re
import threading
import time
from typing import Iterator, List, Optional

import requests

from . import config

API = "https://api.sermonaudio.com/v2/node"
SITE = "https://www.sermonaudio.com"
FEED = "https://feed.sermonaudio.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"

session = requests.Session()
session.headers["User-Agent"] = UA
_key_lock = threading.Lock()


class ApiError(RuntimeError):
    pass


# ---------- auth ----------

def _site_key_path():
    return config.config_dir() / "site_key.txt"


_UUID = r"[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}"
_KEY_NAME = re.compile(r"(?:public|api)_?key", re.I)


def _key_from_nuxt_payload(html: str) -> Optional[str]:
    """Nuxt 3 serializes state as a flat JSON array; dicts point at values by index."""
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    for item in data:
        if not isinstance(item, dict):
            continue
        for name, idx in item.items():
            if _KEY_NAME.search(name) and isinstance(idx, int) and 0 <= idx < len(data):
                value = data[idx]
                if isinstance(value, str) and re.fullmatch(_UUID, value):
                    return value
    return None


def _key_from_text(text: str) -> Optional[str]:
    """apiKey:"…" / publicKey: '…' / "api_key": "…" inlined in HTML or JS."""
    m = re.search(r'(?:api|public)_?key["\']?\s*[:=]\s*["\'](' + _UUID + ')', text, re.I)
    return m.group(1) if m else None


def fetch_site_key() -> str:
    """Find the public API key sermonaudio.com uses: page state first, then its JS bundles."""
    resp = session.get(SITE + "/", timeout=30)
    resp.raise_for_status()
    html = resp.text
    key = _key_from_nuxt_payload(html) or _key_from_text(html)
    if not key:
        for src in list(dict.fromkeys(re.findall(r'(?:src|href)="(/_nuxt/[^"]+\.js)"', html)))[:40]:
            try:
                key = _key_from_text(session.get(SITE + src, timeout=30).text)
            except requests.RequestException:
                continue
            if key:
                break
    if not key:
        raise ApiError("Could not find the API key on sermonaudio.com; the site layout may have changed. "
                       "You can set your own with: sapy config set api_key YOUR-KEY")
    path = _site_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key, encoding="utf-8")
    return key


def get_api_key(force_refresh: bool = False) -> str:
    own = os.environ.get("SAPY_API_KEY") or config.load_config().get("api_key")
    if own:
        return own.strip()
    if not force_refresh:
        try:
            cached = _site_key_path().read_text(encoding="utf-8").strip()
            if cached:
                return cached
        except OSError:
            pass
    return fetch_site_key()


def _ensure_key(force_refresh: bool = False) -> None:
    with _key_lock:
        if force_refresh or "X-API-Key" not in session.headers:
            session.headers["X-API-Key"] = get_api_key(force_refresh)


def _get(path: str, params: dict = None) -> dict:
    _ensure_key()
    url = f"{API}/{path.lstrip('/')}"
    for attempt in range(4):
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code == 401 and attempt == 0:
            # Site key rotated; fetch a fresh one once. Your own key is never replaced.
            if os.environ.get("SAPY_API_KEY") or config.load_config().get("api_key"):
                raise ApiError("SermonAudio rejected your API key (HTTP 401).")
            _ensure_key(force_refresh=True)
            continue
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 404:
            return {}
        if not resp.ok:
            raise ApiError(f"HTTP {resp.status_code} from {url}")
        return resp.json()
    raise ApiError(f"Gave up on {url}")


# ---------- queries ----------

def search(query: str, sort_by: Optional[str] = None, page_size: int = 10) -> dict:
    """Mixed search: speakerResults, broadcasterResults, seriesResults, sermonResults."""
    params = {"query": query, "liteBroadcaster": "true", "pageSize": page_size}
    if sort_by:
        params["sortBy"] = sort_by
    return _get("search", params)


def sermon(sermon_id: str) -> dict:
    return _get(f"sermons/{sermon_id}")


def sermons(page_size: int = 100, limit: Optional[int] = None, **filters) -> Iterator[dict]:
    """
    Page through node/sermons. Useful filters: speakerID, broadcasterID,
    series (takes the series ID), sermonIDs (comma list), searchKeyword,
    sortBy (newest, oldest, ...).
    """
    seen = set()
    page = 1
    while True:
        params = {"page": page, "pageSize": page_size, "liteBroadcaster": "true", **filters}
        data = _get("sermons", params)
        results = data.get("results") or []
        for item in results:
            sid = str(item.get("sermonID"))
            if sid in seen:
                continue
            seen.add(sid)
            yield item
            if limit and len(seen) >= limit:
                return
        if len(results) < page_size or not data.get("next", True):
            return
        page += 1


def series(series_id: str) -> dict:
    """Series details: title, count, broadcaster, earliest/latest dates."""
    return _get(f"series/{series_id}")


def broadcaster_series(broadcaster_id: str) -> Iterator[dict]:
    page = 1
    while True:
        data = _get(f"broadcasters/{broadcaster_id}/series", {"page": page, "pageSize": 100})
        results = data.get("results") or []
        yield from results
        if len(results) < 100 or not data.get("next", True):
            return
        page += 1


def count_sermons(**filters) -> int:
    data = _get("sermons", {"page": 1, "pageSize": 1, "liteBroadcaster": "true", **filters})
    return int(data.get("totalCount") or 0)


def sermons_by_ids(ids: List[str]) -> List[dict]:
    """Full records for a list of IDs, kept in the given order."""
    found = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        for item in sermons(page_size=len(chunk), sermonIDs=",".join(chunk)):
            found[str(item.get("sermonID"))] = item
    return [found[i] for i in ids if i in found]


# ---------- series (public RSS feed, no key needed) ----------

FEED_CAP = 100  # the feed only carries the newest 100 sermons


def series_feed(series_id: str) -> dict:
    """Returns {"title": ..., "items": [{"sermonID", "title", "speaker", "date"}]}, oldest first."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    resp = session.get(f"{FEED}/{series_id}", timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    channel = root.find("channel")
    itunes = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
    items = []
    for it in channel.findall("item"):
        link = " ".join(filter(None, [
            it.findtext("link"), it.findtext("guid"),
            (it.find("enclosure").get("url") if it.find("enclosure") is not None else ""),
        ]))
        m = re.search(r"(?:/sermons/|/media/[^/]+/[^/]+/|SID=)(\d{6,})", link)
        if not m:
            continue
        date = ""
        try:
            date = parsedate_to_datetime(it.findtext("pubDate")).date().isoformat()
        except (TypeError, ValueError):
            pass
        items.append({
            "sermonID": m.group(1),
            "title": (it.findtext("title") or "").strip(),
            "speaker": (it.findtext(f"{itunes}author") or it.findtext("author") or "").strip(),
            "date": date,
        })
    items.sort(key=lambda x: x["date"])
    return {"title": (channel.findtext("title") or f"Series {series_id}").strip(), "items": items}
