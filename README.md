# SermonAudio Downloader Suite

A concept set of open tools to download sermons, series, and entire speaker libraries from SermonAudio.com.

## Features

- **CLI:** Search and download from the command line.
- **GUI:** A fast, native desktop interface built with Flet.
- **Auto-Auth:** Automatically fetches and maintains the required API keys.
- **Bulk Downloading:** Download entire speaker libraries, broadcaster catalogs, or sermon series.
- **Smart Tagging:** Automatically renames files based on metadata (where available).

## Installation

1.  Clone the repo.
2.  Install dependencies:
    ```bash
    pip install requests beautifulsoup4 flet mutagen
    ```

## Usage

### 1. Graphical Interface (GUI)
The easiest way to use the tools.
```bash
python sa_gui.py
```

### 2. Command Line Interface (CLI)

**Search:**
```bash
python sa_cli.py search "paul washer"
python sa_cli.py search "gospel of john" --newest
```

**Download a Single Sermon:**
```bash
python sa_cli.py download https://www.sermonaudio.com/sermons/21422247165025
python sa_cli.py download 21422247165025 --audio high
```

**Download Everything by a Speaker:**
```bash
python sa_cli.py speaker 48786
# or via URL
python sa_cli.py speaker https://www.sermonaudio.com/speakers/48786/
```

**Download a Broadcaster:**
```bash
python sa_cli.py broadcaster ghbc
```

**Download a Series:**
```bash
python sa_cli.py series https://www.sermonaudio.com/series/36435
```

## Authentication
The tool automatically handles API keys. If you encounter auth issues, simply delete `auth.txt` and the script will fetch a fresh key on the next run.
