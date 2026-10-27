# sermonaudio.py (`sapy`)

Search and download sermons from [SermonAudio.com](https://www.sermonaudio.com) — single sermons,
everything by a speaker, whole series, or a church's full catalog — from the terminal or a desktop app.

```bash
pip install sermonaudio.py            # command line
pip install "sermonaudio.py[gui]"     # plus the desktop app (PySide6)
```

## Usage

Run `sapy` with nothing after it for interactive mode: type a search or paste a link, pick a result, download.

Or hand it anything and it works out what it is:

```bash
sapy https://www.sermonaudio.com/sermons/21422247165025   # one sermon
sapy https://www.sermonaudio.com/speakers/48786           # everything by a speaker
sapy https://www.sermonaudio.com/series/151324            # a whole series, in order
sapy https://www.sermonaudio.com/broadcasters/heartcry    # a church's full catalog
sapy 21422247165025                                       # a sermon ID
sapy paul washer                                          # search, then pick
```

Name things directly:

```bash
sapy speaker "paul washer"       # by name or ID
sapy series "studies in proverbs"  # by name, ID, link or podcast feed URL
sapy broadcaster heartcry
sapy search gospel of john --newest
```

### Series

Series are the main event: `sapy series …` gets **every** sermon in the series in preaching order
(straight from SermonAudio's API — not the podcast feed, which stops at 100), numbers them as tracks
(`1/135`, `2/135` …) so players keep them in order, and keeps them in one folder even when a series
has guest speakers.

Don't know the series? Pick from a speaker's or church's list:

```bash
sapy speaker "paul washer" -s     # menu of his 144 series, type to filter
sapy broadcaster heartcry -s -l   # just list a church's series with their IDs
```

In interactive mode, picking a speaker or church gives you the same `[s] pick a series` option.

### Video and quality

```bash
sapy <sermon> -F                  # which formats exist, with sizes
sapy <sermon> --video             # 720p (default; change with `sapy config set video_quality 1080p`)
sapy <sermon> --video 1080p       # or 360p, 720p, best
sapy speaker "paul washer" --video 360p -n 10
sapy <sermon> --small             # low-quality audio, about a sixth the size
```

If a sermon doesn't have the quality you asked for, sapy takes the nearest one it does have
(older uploads often only have one video size) and names the file after what you actually got,
e.g. `… [720p].mp4`. Sermons without any video are skipped in bulk video downloads.
In interactive mode you get a menu of the formats each sermon really has, with sizes.

Options:

| | |
|---|---|
| `-o DIR` | download folder (default: `download_dir` setting, else the current folder) |
| `--video [360p\|720p\|1080p\|best]` | video instead of audio (default 720p) |
| `-q QUALITY` | audio `low`/`high`, or a video quality |
| `--small` | low-quality audio: about a sixth the size, but muffled (default is full quality) |
| `-F` | show a sermon's available formats and sizes |
| `-s` | pick one of a speaker's or church's series |
| `-n N` | only the newest N (speaker/broadcaster) or first N (series; add `--newest` for the latest N) |
| `-l` | list what would be downloaded and stop |
| `-y` | don't ask for confirmation |
| `-j N` | parallel downloads (default 3) |
| `sapy gui` | open the desktop app |

### What "smart" means here

- **Organized:** `Speaker/Series/2020-04-20 - Title.mp3`. Sermons outside a series go straight in the speaker folder.
- **Re-runnable:** files you already have are skipped, so running the same speaker or series again only gets new sermons.
- **Resumable:** Ctrl+C or a dropped connection is fine — run it again and partial files continue where they stopped.
- **Tagged:** title, speaker, series, church and date are written into each file's tags, so music players sort them properly.

## Settings

```bash
sapy config                                   # show settings and where they're stored
sapy config set download_dir ~/Sermons
sapy config set quality low                   # default audio quality (high unless you change it)
sapy config set video_quality 1080p           # default video quality
sapy config set workers 4
sapy config set api_key YOUR-KEY              # use your own SermonAudio API key
```

Settings live in `~/.config/sapy/config.json` (`%APPDATA%\sapy` on Windows).

## API key

Downloads themselves don't need a key; search and listings do. By default sapy uses the public key
that sermonaudio.com itself uses, cached in `~/.config/sapy/site_key.txt`. When SermonAudio rotates
it, the next request gets refused, and sapy automatically re-reads the current key from the site
(from the page's data, or its scripts if they move it) and carries on — nothing for you to do. If you have your own key, set it with `sapy config set api_key ...` or the
`SAPY_API_KEY` environment variable and it takes priority.

## Development

```bash
pip install -e ".[gui]"
python -m unittest discover -s tests
```
