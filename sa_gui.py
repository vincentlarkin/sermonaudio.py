import flet as ft
import threading
import sys
import io
import os
import time
import sa_search
import sa_dl
import sa_speaker
import sa_broadcaster
import sa_auth
import sa_config

# Redirect stdout to capture print statements for the GUI log
class TextRedirector(io.StringIO):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def write(self, string):
        # Schedule the update on the main thread
        self.text_widget.value += string
        self.text_widget.update()
        # Also print to real stdout for debugging
        sys.__stdout__.write(string)

def main(page: ft.Page):
    # Load Config
    app_config = sa_config.load_config()
    
    page.title = "SermonAudio Downloader"
    page.theme_mode = ft.ThemeMode.DARK if app_config.get("theme_mode") == "dark" else ft.ThemeMode.LIGHT
    page.padding = 20
    page.window_width = 1000
    page.window_height = 800
    
    # Global state
    download_dir = app_config.get("download_dir", os.getcwd())
    download_queue = [] # List of task objects
    
    # --- Components ---
    
    # Header
    header = ft.Text("SermonAudio Downloader", size=30, weight="bold")
    
    # Auth Status
    auth_status = ft.Text("Checking API Key...", color="orange", size=12)
    
    def check_auth():
        try:
            key = sa_auth.get_api_key()
            auth_status.value = f"API Key Active: ...{key[-6:]}"
            auth_status.color = "green"
        except Exception as e:
            auth_status.value = f"Auth Error: {e}"
            auth_status.color = "red"
        page.update()

    # Log Area
    log_output = ft.Text(value="", size=12, font_family="Consolas", selectable=True)
    log_container = ft.Column(
        [log_output], 
        scroll=ft.ScrollMode.AUTO, 
        height=150,
        width=float("inf")
    )
    log_card = ft.Container(
        content=log_container,
        border=ft.border.all(1, "outline"),
        border_radius=5,
        padding=10,
        bgcolor="surfaceVariant"
    )
    
    logs_visible = app_config.get("show_logs", True)
    
    def toggle_logs(e):
        nonlocal logs_visible
        logs_visible = not logs_visible
        log_card.visible = logs_visible
        btn_toggle_logs.text = "Hide Logs" if logs_visible else "Show Logs"
        sa_config.save_config("show_logs", logs_visible)
        page.update()

    btn_toggle_logs = ft.TextButton("Hide Logs" if logs_visible else "Show Logs", on_click=toggle_logs)
    log_card.visible = logs_visible

    # Redirect print
    sys.stdout = TextRedirector(log_output)

    # --- Download Queue UI ---
    queue_list = ft.ListView(expand=True, spacing=5, padding=10)
    
    def open_file(path):
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.call(('open', path))
            else:
                import subprocess
                subprocess.call(('xdg-open', path))
        except Exception as e:
            print(f"[GUI] Error opening file: {e}")

    def add_to_queue(name, status="Pending"):
        # Creates a UI row for the download
        pb = ft.ProgressBar(width=200, value=0)
        status_text = ft.Text(status, size=12, width=100)
        play_btn = ft.IconButton(
            icon=ft.Icons.PLAY_ARROW, 
            visible=False,
            tooltip="Play File"
        )
        
        row = ft.Row([
            ft.Icon(ft.Icons.DOWNLOAD),
            ft.Text(name, expand=True, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
            status_text,
            pb,
            play_btn
        ])
        queue_list.controls.append(row)
        page.update()
        return pb, status_text, play_btn

    # --- Search Tab ---
    search_query = ft.TextField(label="Search Query", expand=True)
    search_results = ft.ListView(expand=True, spacing=10, padding=10)

    def run_search(e):
        query = search_query.value
        if not query:
            return
        
        search_results.controls.clear()
        search_results.controls.append(ft.Text("Searching...", italic=True))
        page.update()

        def _search_task():
            try:
                data = sa_search.perform_search(query)
                def update_ui():
                    search_results.controls.clear()
                    
                    # Sermons
                    if data.get('sermonResults'):
                        search_results.controls.append(ft.Text("Top Sermons", weight="bold", size=16))
                        for s in data['sermonResults'][:5]:
                            sid = s.get('sermonID')
                            title = s.get('fullTitle', 'Untitled')
                            speaker = s.get('speaker', {}).get('displayName', 'Unknown')
                            
                            search_results.controls.append(
                                ft.Row([
                                    ft.Column([
                                        ft.Text(title, weight="bold"),
                                        ft.Text(f"{speaker} (ID: {sid})", size=12, color="grey")
                                    ], expand=True),
                                    ft.IconButton(
                                        icon=ft.Icons.DOWNLOAD, 
                                        tooltip="Download Audio",
                                        on_click=lambda _, s=sid, t=title: start_download_single(s, title=t)
                                    )
                                ])
                            )
                            search_results.controls.append(ft.Divider())

                    # Broadcasters
                    if data.get('broadcasterResults'):
                        search_results.controls.append(ft.Text("Broadcasters", weight="bold", size=16))
                        for b in data['broadcasterResults'][:3]:
                            bid = b.get('broadcasterID')
                            name = b.get('displayName')
                            search_results.controls.append(
                                ft.Row([
                                    ft.Text(f"{name} ({bid})", expand=True),
                                    ft.ElevatedButton("Download All", on_click=lambda _, b=bid, n=name: start_download_broadcaster(b, n))
                                ])
                            )
                            search_results.controls.append(ft.Divider())

                    # Speakers
                    if data.get('speakerResults'):
                        search_results.controls.append(ft.Text("Speakers", weight="bold", size=16))
                        for sp in data['speakerResults'][:3]:
                            spid = str(sp.get('speakerID'))
                            name = sp.get('displayName')
                            search_results.controls.append(
                                ft.Row([
                                    ft.Text(f"{name} ({spid})", expand=True),
                                    ft.ElevatedButton("Download All", on_click=lambda _, s=spid, n=name: start_download_speaker(s, n))
                                ])
                            )
                            search_results.controls.append(ft.Divider())
                    
                    page.update()

                update_ui()

            except Exception as e:
                def show_error():
                    search_results.controls.append(ft.Text(f"Error: {e}", color="red"))
                    page.update()
                show_error()
            
        threading.Thread(target=_search_task, daemon=True).start()

    # --- Download Logic ---
    
    dl_target = ft.TextField(label="URL or ID", expand=True)
    
    dl_format = ft.Dropdown(
        options=[ft.dropdown.Option("Audio"), ft.dropdown.Option("Video")],
        value="Audio",
        width=150,
        label="Format"
    )
    
    # Updated to include more video options if needed, logic handles mapping
    dl_quality = ft.Dropdown(
        options=[
            ft.dropdown.Option("Low"), 
            ft.dropdown.Option("High")
        ],
        value="Low",
        width=150,
        label="Quality"
    )

    def on_format_change(e):
        fmt = dl_format.value
        if fmt == "Audio":
            dl_quality.options = [
                ft.dropdown.Option("Low"), 
                ft.dropdown.Option("High")
            ]
            # Reset if current selection is invalid for Audio
            if dl_quality.value == "1080p":
                dl_quality.value = "Low"
        else:
            dl_quality.options = [
                ft.dropdown.Option("Low"), 
                ft.dropdown.Option("High"),
                ft.dropdown.Option("1080p")
            ]
        page.update()

    dl_format.on_change = on_format_change

    def start_download_single(target, title=None):
        # Switch to queue tab
        tabs.selected_index = 2
        page.update()
        
        display_name = title or target
        pb, status_lbl, play_btn = add_to_queue(f"{display_name} ({dl_format.value})")
        
        fmt = dl_format.value.lower()
        qual = dl_quality.value.lower()

        def _task():
            status_lbl.value = "Starting..."
            page.update()
            
            final_path = None

            try:
                sid = sa_dl.extract_sermon_id(target)
                
                # Video Availability Check
                if fmt == "video" and sid:
                    status_lbl.value = "Checking Info..."
                    page.update()
                    info = sa_search.get_sermon_info(sid)
                    if not info:
                        status_lbl.value = "Info Error"
                        status_lbl.color = "red"
                        page.update()
                        return
                    
                    has_video = info.get('hasVideo', False)
                    media_vid = info.get('media', {}).get('video', [])
                    if not has_video or not media_vid:
                        status_lbl.value = "No Video"
                        status_lbl.color = "red"
                        page.update()
                        return

                status_lbl.value = "Downloading..."
                page.update()

                def progress_hook(current, total):
                    if total > 0:
                        pb.value = current / total
                        status_lbl.value = f"{int((current/total)*100)}%"
                    page.update()

                # Download Logic
                if fmt == "video":
                    sid = sid or target 
                    url = sa_dl.build_video_url(sid, qual)
                    final_path = sa_dl.download_file(url, download_dir, media_type="video", quality=qual, progress_callback=progress_hook)
                else:
                    if sid:
                        final_path = sa_dl.download_audio_with_fallback(sid, download_dir, preferred_quality=qual, progress_callback=progress_hook)
                    else:
                        final_path = sa_dl.download_file(target, download_dir, media_type="audio", quality=qual, progress_callback=progress_hook)
                
                status_lbl.value = "Complete"
                status_lbl.color = "green"
                pb.value = 1
                
                if final_path and os.path.exists(final_path):
                    play_btn.visible = True
                    play_btn.on_click = lambda _: open_file(str(final_path))
                
                page.update()
                        
            except Exception as e:
                print(f"[GUI] Error: {e}")
                status_lbl.value = "Error"
                status_lbl.color = "red"
                page.update()
        
        threading.Thread(target=_task, daemon=True).start()

    def start_download_speaker(sid, name=None):
        tabs.selected_index = 2
        page.update()
        
        name = name or sid
        pb, status_lbl, play_btn = add_to_queue(f"Speaker: {name}")
        status_lbl.value = "Fetching list..."
        pb.type = ft.ProgressBarOperation.INDETERMINATE
        page.update()

        def _task():
            try:
                speaker_name = sa_speaker.get_speaker_name(sid)
                ids = sa_speaker.collect_sermon_ids_via_node(sid, page_size=50, max_pages=2) # limit for demo
                
                status_lbl.value = f"Found {len(ids)}"
                pb.type = ft.ProgressBarOperation.DETERMINATE
                pb.value = 0
                page.update()
                
                for idx, s in enumerate(ids):
                    status_lbl.value = f"{idx+1}/{len(ids)}"
                    pb.value = (idx) / len(ids)
                    page.update()
                    
                    # We don't have individual progress bars for bulk yet, just overall
                    sa_speaker.download_sermon_audio(s, download_dir, speaker_name)
                
                status_lbl.value = "Complete"
                status_lbl.color = "green"
                pb.value = 1
                
                # Open folder button for bulk?
                play_btn.icon = ft.Icons.FOLDER
                play_btn.tooltip = "Open Folder"
                play_btn.visible = True
                # Construct expected path (heuristic)
                folder_path = os.path.join(download_dir, sa_dl.sanitize_filename(speaker_name))
                play_btn.on_click = lambda _: open_file(folder_path)
                
                page.update()

            except Exception as e:
                print(f"[GUI] Error: {e}")
                status_lbl.value = "Error"
                status_lbl.color = "red"
        
        threading.Thread(target=_task, daemon=True).start()

    def start_download_broadcaster(bid, name=None):
        tabs.selected_index = 2
        page.update()
        
        name = name or bid
        pb, status_lbl, play_btn = add_to_queue(f"Broadcaster: {name}")
        status_lbl.value = "Fetching list..."
        pb.type = ft.ProgressBarOperation.INDETERMINATE
        page.update()

        def _task():
            try:
                bname = sa_broadcaster.get_broadcaster_name(bid)
                ids = sa_broadcaster.collect_sermon_ids_via_broadcaster(bid, page_size=50, max_pages=2)
                
                status_lbl.value = f"Found {len(ids)}"
                pb.type = ft.ProgressBarOperation.DETERMINATE
                pb.value = 0
                page.update()

                for idx, s in enumerate(ids):
                    status_lbl.value = f"{idx+1}/{len(ids)}"
                    pb.value = (idx) / len(ids)
                    page.update()
                    sa_broadcaster.download_sermon_audio(s, download_dir, bname)
                
                status_lbl.value = "Complete"
                status_lbl.color = "green"
                pb.value = 1
                
                # Open folder button for bulk
                play_btn.icon = ft.Icons.FOLDER
                play_btn.tooltip = "Open Folder"
                play_btn.visible = True
                folder_path = os.path.join(download_dir, sa_dl.sanitize_filename(bname))
                play_btn.on_click = lambda _: open_file(folder_path)
                
                page.update()
            except Exception as e:
                print(f"[GUI] Error: {e}")
                status_lbl.value = "Error"
                status_lbl.color = "red"
        
        threading.Thread(target=_task, daemon=True).start()


    # Layout
    tabs = ft.Tabs(
        selected_index=0,
        animation_duration=300,
        tabs=[
            ft.Tab(
                text="Search",
                icon=ft.Icons.SEARCH,
                content=ft.Column([
                    ft.Container(height=10),
                    ft.Row([search_query, ft.IconButton(ft.Icons.ARROW_FORWARD, on_click=run_search)]),
                    search_results
                ])
            ),
            ft.Tab(
                text="Manual Download",
                icon=ft.Icons.DOWNLOAD,
                content=ft.Column([
                    ft.Container(height=20),
                    ft.Row([
                        dl_target, 
                        ft.Container(width=10), 
                        dl_format, 
                        ft.Container(width=10),
                        dl_quality,
                        ft.Container(width=10),
                        ft.ElevatedButton("Download", on_click=lambda _: start_download_single(dl_target.value), height=50)
                    ], alignment=ft.MainAxisAlignment.CENTER),
                    ft.Text("Enter a Sermon ID or URL above.", color="grey", italic=True)
                ])
            ),
            ft.Tab(
                text="Queue",
                icon=ft.Icons.LIST,
                content=queue_list
            )
        ],
        expand=True,
    )
    
    # --- Folder Picker ---
    def on_folder_result(e: ft.FilePickerResultEvent):
        if e.path:
            nonlocal download_dir
            download_dir = e.path
            folder_text_ref.value = download_dir
            folder_text_ref.update()
            sa_config.save_config("download_dir", download_dir)
            print(f"[Settings] Download folder changed to: {download_dir}")

    folder_picker = ft.FilePicker(on_result=on_folder_result)
    page.overlay.append(folder_picker)
    
    folder_text_ref = ft.TextField(value=download_dir, read_only=True, text_size=12, border_color="grey")

    # Settings Dialog
    def open_settings(e):
        def close_dlg(e):
            page.close(dlg)

        folder_text_ref.value = download_dir

        def refresh_api_key(e):
            try:
                sa_auth.get_api_key(force_refresh=True)
                check_auth() # Update UI status
                page.snack_bar = ft.SnackBar(ft.Text("API Key Refreshed!"))
                page.snack_bar.open = True
                page.update()
            except Exception as err:
                page.snack_bar = ft.SnackBar(ft.Text(f"Error refreshing key: {err}"), bgcolor="red")
                page.snack_bar.open = True
                page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("Settings"),
            content=ft.Container(
                width=550,
                height=350,
                content=ft.Row([
                    # Left Side: Settings
                    ft.Container(
                        expand=1,
                        padding=10,
                        content=ft.Column([
                            ft.Text("Preferences", weight="bold"),
                            ft.Divider(),
                            ft.Row([
                                ft.Text("Show Logs:"),
                                ft.Switch(value=logs_visible, on_change=lambda e: toggle_logs(e))
                            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                            ft.Container(height=20),
                            ft.Text("Default Download Folder:", size=12),
                            folder_text_ref,
                            ft.ElevatedButton("Change Folder", on_click=lambda _: folder_picker.get_directory_path()),
                            ft.Container(height=20),
                            ft.Text("API Key:", size=12),
                            ft.ElevatedButton("Refresh Key", on_click=refresh_api_key)
                        ])
                    ),
                    ft.VerticalDivider(),
                    # Right Side: Info
                    ft.Container(
                        expand=1,
                        padding=10,
                        content=ft.Column([
                            ft.Text("About", weight="bold"),
                            ft.Divider(),
                            ft.Text("SermonAudio Downloader", size=16),
                            ft.Text("Concept & Implementation by Vincent L.", size=12, italic=True),
                            ft.Text("2025", size=12),
                            ft.Container(height=20),
                            ft.Text("A powerful tool to archive sermons, series, and speaker libraries.", size=12),
                            ft.Container(height=20),
                            ft.Text("v1.0.0", color="grey", size=10)
                        ], alignment=ft.MainAxisAlignment.START)
                    )
                ])
            ),
            actions=[
                ft.TextButton("Close", on_click=close_dlg),
            ],
        )
        page.open(dlg)

    settings_btn = ft.IconButton(ft.Icons.SETTINGS, on_click=open_settings)

    page.add(
        ft.Row([header, ft.Container(expand=True), auth_status, settings_btn], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Divider(),
        ft.Container(content=tabs, expand=True),
        ft.Row([ft.Text("Logs:", weight="bold"), ft.Container(expand=True), btn_toggle_logs]),
        log_card
    )

    check_auth()

if __name__ == "__main__":
    ft.app(target=main)
