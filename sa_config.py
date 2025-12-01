import json
import os

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "download_dir": os.getcwd(),
    "show_logs": True,
    "theme_mode": "dark"
}

def load_config():
    """Load configuration from JSON file."""
    if not os.path.exists(CONFIG_FILE):
        return DEFAULT_CONFIG.copy()
    
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
            # Merge with defaults to ensure all keys exist
            merged = DEFAULT_CONFIG.copy()
            merged.update(config)
            
            # Validate download_dir exists, else fallback
            if not os.path.exists(merged["download_dir"]):
                merged["download_dir"] = os.getcwd()
                
            return merged
    except Exception as e:
        print(f"[Config] Error loading config: {e}")
        return DEFAULT_CONFIG.copy()

def save_config(key, value):
    """Update a single setting and save to file."""
    config = load_config()
    config[key] = value
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"[Config] Error saving config: {e}")



