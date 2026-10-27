"""Work out what a user-supplied link, ID or phrase refers to."""

import re
import urllib.parse
from dataclasses import dataclass
from typing import Optional

KINDS = ("sermon", "speaker", "series", "broadcaster")


@dataclass
class Target:
    kind: str                 # sermon | speaker | series | broadcaster | search
    value: str                # ID, or the query text for search / speaker-by-name
    media: Optional[str] = None    # "audio"/"video" when a direct media link was given
    quality: Optional[str] = None
    by_name: bool = False     # speaker/series given as a name, needs a search


def resolve(text: str, kind: Optional[str] = None) -> Target:
    text = text.strip()
    if text.startswith("www.") or text.startswith("sermonaudio.com"):
        text = "https://" + text

    if text.startswith(("http://", "https://")):
        t = _from_url(text)
        if t:
            return t
        raise ValueError(f"Don't know how to handle this link: {text}")

    if kind:
        if kind in ("speaker", "series") and not text.isdigit():
            return Target(kind, text, by_name=True)
        return Target(kind, text)

    if re.fullmatch(r"\d{9,}", text):
        return Target("sermon", text)
    return Target("search", text)


def _from_url(url: str) -> Optional[Target]:
    u = urllib.parse.urlparse(url)
    path = u.path
    query = {k.lower(): v[0] for k, v in urllib.parse.parse_qs(u.query).items()}

    m = re.search(r"/media/(audio|video)/([^/]+)/(\d+)\.(mp3|mp4)$", path)
    if m:
        return Target("sermon", m.group(3), media=m.group(1), quality=m.group(2))

    # Podcast feeds: feed.sermonaudio.com/<seriesID>
    m = re.fullmatch(r"/(\d+)/?", path)
    if u.netloc.startswith("feed.") and m:
        return Target("series", m.group(1))

    for kind, pattern in (("sermon", r"/sermons/(\d+)"), ("speaker", r"/speakers/(\d+)"),
                          ("series", r"/series/(\d+)"), ("broadcaster", r"/broadcasters/([^/?#]+)")):
        m = re.search(pattern, path)
        if m:
            return Target(kind, urllib.parse.unquote(m.group(1)))

    # Church "solo" sites: /solo/<broadcasterID>/...
    m = re.match(r"/solo/([^/]+)/?$", path)
    if m:
        return Target("broadcaster", m.group(1))

    # Old ASP-era links
    if "sid" in query and query["sid"].isdigit():
        return Target("sermon", query["sid"])
    if "speakeronly" in query or "speaker" in query:
        return Target("speaker", query.get("speaker") or query["speakeronly"], by_name=True)
    if "sourceid" in query:
        return Target("broadcaster", query["sourceid"])
    return None
