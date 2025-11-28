import flet as ft
import threading
import sys
import io
import sa_search
import sa_dl
import sa_speaker
import sa_broadcaster
import sa_auth

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
    page.title = "SermonAudio Downloader"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 20
    page.window_width = 800
    page.window_height = 700

    # --- Components ---
    
    # Header
    header = ft.Text("SermonAudio Downloader", size=30, weight="bold")
    
    # Auth Status
    auth_status = ft.Text("Checking API Key...", color="orange")
    
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
    log_output = ft.Text(value="", size=12, font_family="Consolas")
    log_container = ft.Column(
        [log_output], 
        scroll=ft.ScrollMode.AUTO, 
        height=200,
        width=float("inf")
    )
    log_card = ft.Container(
        content=log_container,
        border=ft.border.all(1, "outline"),
        border_radius=5,
        padding=10,
        bgcolor="surfaceVariant"
    )

    # redirect print
    sys.stdout = TextRedirector(log_output)

    # Search Tab
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
                # Schedule UI updates on the main thread to avoid "Control must be added to the page first" issues
                # when manipulating controls from a background thread
                def update_ui():
                    search_results.controls.clear()
                    
                    # Sermons
                    if data.get('sermonResults'):
                        search_results.controls.append(ft.Text("Top Sermons", weight="bold", size=16))
                        for s in data['sermonResults'][:5]:
                            sid = s.get('sermonID')
                            title = s.get('fullTitle', 'Untitled')
                            speaker = s.get('speaker', {}).get('displayName', 'Unknown')
                            
                            # Add a row with download button
                            search_results.controls.append(
                                ft.Row([
                                    ft.Column([
                                        ft.Text(title, weight="bold"),
                                        ft.Text(f"{speaker} (ID: {sid})", size=12, color="grey")
                                    ], expand=True),
                                    ft.IconButton(
                                        icon=ft.Icons.DOWNLOAD, 
                                        on_click=lambda _, s=sid: start_download_single(s)
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
                                    ft.ElevatedButton("Download All", on_click=lambda _, b=bid: start_download_broadcaster(b))
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
                                    ft.ElevatedButton("Download All", on_click=lambda _, s=spid: start_download_speaker(s))
                                ])
                            )
                            search_results.controls.append(ft.Divider())
                    
                    page.update()

                # Run the UI update on the main thread (Flet uses threading, but page.update is thread-safe if controls are attached?)
                # Actually Flet page.update() IS thread-safe but modifying lists of attached controls might be tricky.
                # Let's just call the update function.
                update_ui()

            except Exception as e:
                def show_error():
                    search_results.controls.append(ft.Text(f"Error: {e}", color="red"))
                    page.update()
                show_error()
            
        threading.Thread(target=_search_task, daemon=True).start()

    # Download Tab
    dl_target = ft.TextField(label="URL or ID", expand=True)
    dl_type = ft.Dropdown(
        options=[ft.dropdown.Option("audio"), ft.dropdown.Option("video")],
        value="audio",
        width=100
    )

    def start_download_single(target):
        page.snack_bar = ft.SnackBar(ft.Text(f"Starting download for {target}"))
        page.snack_bar.open = True
        page.update()
        
        def _task():
            print(f"\n[GUI] Downloading single: {target}")
            try:
                # Reuse CLI logic roughly
                if dl_type.value == "video":
                    sid = sa_dl.extract_sermon_id(target) or target
                    # Basic video heuristic
                    url = sa_dl.build_video_url(sid, "low")
                    sa_dl.download_file(url, ".", media_type="video", quality="low")
                else:
                    sid = sa_dl.extract_sermon_id(target)
                    if sid:
                        sa_dl.download_audio_with_fallback(sid, ".")
                    else:
                        # Assume URL
                        sa_dl.download_file(target, ".", media_type="audio")
            except Exception as e:
                print(f"[GUI] Error: {e}")
        
        threading.Thread(target=_task, daemon=True).start()

    def start_download_speaker(sid):
        def _task():
            print(f"\n[GUI] Downloading speaker: {sid}")
            try:
                speaker_name = sa_speaker.get_speaker_name(sid)
                ids = sa_speaker.collect_sermon_ids_via_node(sid, page_size=25, max_pages=2) # limit for demo
                root = os.path.abspath(".")
                for idx, s in enumerate(ids):
                    sa_speaker.download_sermon_audio(s, root, speaker_name)
            except Exception as e:
                print(f"[GUI] Error: {e}")
        threading.Thread(target=_task, daemon=True).start()

    def start_download_broadcaster(bid):
        def _task():
            print(f"\n[GUI] Downloading broadcaster: {bid}")
            try:
                name = sa_broadcaster.get_broadcaster_name(bid)
                ids = sa_broadcaster.collect_sermon_ids_via_broadcaster(bid, page_size=25, max_pages=2)
                root = os.path.abspath(".")
                for idx, s in enumerate(ids):
                    sa_broadcaster.download_sermon_audio(s, root, name)
            except Exception as e:
                print(f"[GUI] Error: {e}")
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
                    ft.Row([search_query, ft.IconButton(ft.Icons.ARROW_FORWARD, on_click=run_search)]),
                    search_results
                ])
            ),
            ft.Tab(
                text="Manual Download",
                icon=ft.Icons.DOWNLOAD,
                content=ft.Column([
                    ft.Row([dl_target, dl_type, ft.ElevatedButton("Go", on_click=lambda _: start_download_single(dl_target.value))]),
                    ft.Text("Enter a Sermon ID or URL above.")
                ])
            ),
        ],
        expand=True,
    )

    page.add(
        ft.Column([
            ft.Row([header, ft.Container(expand=True), auth_status]),
            tabs,
            ft.Text("Logs:", weight="bold"),
            log_card
        ], expand=True)
    )

    check_auth()

if __name__ == "__main__":
    ft.app(target=main)

