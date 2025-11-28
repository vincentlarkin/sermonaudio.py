#!/usr/bin/env python3
"""
sa_cli.py

Unified CLI for SermonAudio tools.
"""

import argparse
import sys

# Import functionality from other modules
import sa_search
import sa_dl
import sa_speaker
import sa_broadcaster
import sa_series

def handle_search(args):
    query = " ".join(args.query)
    print(f"[+] Searching for: '{query}'")
    
    if args.newest:
        print("(Sorting by newest)")
        data = sa_search.perform_search(query, sort_by="newest-published")
        sa_search.print_sermons(data.get('sermonResults', []), "Newest Sermons")
    else:
        data = sa_search.perform_search(query)
        sa_search.print_broadcasters(data.get('broadcasterResults', []))
        sa_search.print_speakers(data.get('speakerResults', []))
        sa_search.print_series(data.get('seriesResults', []))
        sa_search.print_sermons(data.get('sermonResults', []), "Top Sermons")

def handle_download(args):
    target = args.target
    print(f"[+] Downloading: {target}")
    
    # Heuristic to guess type if not specified?
    # For now, just use sa_dl which handles single audio/video
    if args.video:
        # CLI wrapper for sa_dl's logic
        # checking if user passed a quality
        quality = args.video if args.video in ["low", "high", "1080p"] else "low"
        
        if sa_dl.is_media_url(target, ".mp4"):
             q = sa_dl.detect_quality_from_url(target, "video") or quality
             sa_dl.download_file(target, args.out, media_type="video", quality=q)
        else:
            sid = sa_dl.extract_sermon_id(target)
            if not sid:
                print("[!] Invalid sermon ID/URL")
                return
            url = sa_dl.build_video_url(sid, quality)
            sa_dl.download_file(url, args.out, media_type="video", quality=quality)
            
    else:
        # Audio default
        if sa_dl.is_media_url(target, ".mp3"):
             q = sa_dl.detect_quality_from_url(target, "audio") or "low"
             sa_dl.download_file(target, args.out, media_type="audio", quality=q)
        else:
            sid = sa_dl.extract_sermon_id(target)
            if not sid:
                print("[!] Invalid sermon ID/URL")
                return
            sa_dl.download_audio_with_fallback(sid, args.out)

def handle_speaker(args):
    # sa_speaker.py main logic is a bit trapped in main(), let's reuse the functions
    # We need to mimic what main() does
    raw = args.target
    try:
        speaker_id = sa_speaker.extract_speaker_id(raw)
    except ValueError as e:
        print(f"[!] {e}")
        return

    speaker_name = sa_speaker.get_speaker_name(speaker_id)
    print(f"[+] Speaker: {speaker_name} (ID: {speaker_id})")

    ids = sa_speaker.collect_sermon_ids_via_node(speaker_id)
    if not ids:
        print("[!] No sermons found.")
        return

    # De-dupe
    unique_ids = []
    seen = set()
    for x in ids:
        if x not in seen:
            seen.add(x)
            unique_ids.append(x)
            
    print(f"[+] Found {len(unique_ids)} sermons.")
    
    # Download loop
    import os
    root = os.path.abspath(args.out)
    for idx, sid in enumerate(unique_ids, start=1):
        print(f"\n=== [{idx}/{len(unique_ids)}] Downloading sermon {sid} ===")
        try:
            sa_speaker.download_sermon_audio(sid, root, speaker_name)
        except KeyboardInterrupt:
            print("\n[!] Interrupted.")
            break
        except Exception as e:
            print(f"[!] Error: {e}")

def handle_broadcaster(args):
    # Similar to speaker
    raw = args.target
    broadcaster_id = sa_broadcaster.extract_broadcaster_id(raw)
    name = sa_broadcaster.get_broadcaster_name(broadcaster_id)
    print(f"[+] Broadcaster: {name} (ID: {broadcaster_id})")
    
    ids = sa_broadcaster.collect_sermon_ids_via_broadcaster(broadcaster_id)
    if not ids:
        print("[!] No sermons found.")
        return

    unique_ids = []
    seen = set()
    for x in ids:
        if x not in seen:
            seen.add(x)
            unique_ids.append(x)

    print(f"[+] Found {len(unique_ids)} sermons.")
    
    import os
    root = os.path.abspath(args.out)
    for idx, sid in enumerate(unique_ids, start=1):
        print(f"\n=== [{idx}/{len(unique_ids)}] Downloading sermon {sid} ===")
        try:
            sa_broadcaster.download_sermon_audio(sid, root, name)
        except KeyboardInterrupt:
            print("\n[!] Interrupted.")
            break
        except Exception as e:
            print(f"[!] Error: {e}")

def handle_series(args):
    # sa_series mostly uses sys.argv, but exposed download_series(arg)
    # Ideally pass output dir? sa_series currently hardcodes folder name logic.
    # We'll just call the function.
    print(f"[+] Downloading series: {args.target}")
    # Note: sa_series writes to CWD by default or creates a folder.
    # To respect args.out, we might need to chdir or modify sa_series.
    # For now, let's just run it.
    import os
    cwd = os.getcwd()
    try:
        if args.out != ".":
            if not os.path.exists(args.out):
                os.makedirs(args.out)
            os.chdir(args.out)
        sa_series.download_series(args.target)
    finally:
        os.chdir(cwd)

def main():
    parser = argparse.ArgumentParser(description="SermonAudio CLI Suite")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Search
    p_search = subparsers.add_parser("search", help="Search for content")
    p_search.add_argument("query", nargs="+", help="Query string")
    p_search.add_argument("--newest", action="store_true", help="Sort by newest")
    p_search.set_defaults(func=handle_search)

    # Download Single
    p_dl = subparsers.add_parser("download", help="Download a single sermon")
    p_dl.add_argument("target", help="Sermon ID or URL")
    p_dl.add_argument("--video", choices=["low", "high", "1080p"], help="Download video quality")
    p_dl.add_argument("-o", "--out", default=".", help="Output directory")
    p_dl.set_defaults(func=handle_download)

    # Speaker
    p_sp = subparsers.add_parser("speaker", help="Download all sermons from a speaker")
    p_sp.add_argument("target", help="Speaker ID or URL")
    p_sp.add_argument("-o", "--out", default=".", help="Output directory")
    p_sp.set_defaults(func=handle_speaker)

    # Broadcaster
    p_bc = subparsers.add_parser("broadcaster", help="Download all sermons from a broadcaster")
    p_bc.add_argument("target", help="Broadcaster ID or URL")
    p_bc.add_argument("-o", "--out", default=".", help="Output directory")
    p_bc.set_defaults(func=handle_broadcaster)

    # Series
    p_ser = subparsers.add_parser("series", help="Download a sermon series")
    p_ser.add_argument("target", help="Series ID or URL")
    p_ser.add_argument("-o", "--out", default=".", help="Output directory")
    p_ser.set_defaults(func=handle_series)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)

