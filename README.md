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
sapy series 151324
sapy broadcaster heartcry
sapy search gospel of john --newest
```

Options:

| | |
|---|---|
| `-o DIR` | download folder (default: `download_dir` setting, else the current folder) |
| `--video [low\|high\|1080p]` | video instead of audio (default `high` = 720p) |
| `--hq` | high-quality audio (default is low, which is plenty for speech) |
| `-n N` | only the newest N (speaker/broadcaster) or first N (series) |
| `-l` | list what would be downloaded and stop |
| `-y` | don't ask for confirmation |
| `-j N` | parallel downloads (default 3) |
| `sapy gui` | open the desktop app |

### What "smart" means here

- **Organized:** `Speaker/Series/2020-04-20 - Title.mp3`. Sermons outside a series go straight in the speaker folder.
- **Re-runnable:** files you already have are skipped, so running the same speaker or series again only gets new sermons.
- **Resumable:** interrupted downloads continue from where they stopped.
- **Tagged:** title, speaker, series, church and date are written into each file's tags, so music players sort them properly.

## Settings

```bash
sapy config                                   # show settings and where they're stored
sapy config set download_dir ~/Sermons
sapy config set quality high                  # default audio quality
sapy config set workers 4
sapy config set api_key YOUR-KEY              # use your own SermonAudio API key
```

Settings live in `~/.config/sapy/config.json` (`%APPDATA%\sapy` on Windows).

## API key

Downloads themselves don't need a key; search and listings do. By default sapy uses the public key
that sermonaudio.com itself uses, cached in `~/.config/sapy/site_key.txt` and re-read from the site
when it changes. If you have your own key, set it with `sapy config set api_key ...` or the
`SAPY_API_KEY` environment variable and it takes priority.

## Development

```bash
pip install -e ".[gui]"
python -m unittest discover -s tests
```
