#!/usr/bin/env python3
"""
Qt GUI for SermonAudio tools.

Tabs:
1. Search
2. Manual Download
3. Queue
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import traceback
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import api, config as sa_config
from .cli import FORMAT_MENU
from .download import (broadcaster_sermons, clean, download_many, download_sermon, normalize_quality,
                       series_sermons, single_sermon, speaker_sermons)
from .resolve import resolve


def _safe_text(value: Optional[str], fallback: str = "") -> str:
    text = value or fallback
    return str(text).strip()


def _sermon_title(item: dict) -> str:
    return _safe_text(item.get("fullTitle")) or _safe_text(item.get("displayTitle"), "Untitled Sermon")


def _speaker_name(item: dict) -> str:
    return _safe_text(item.get("speaker", {}).get("displayName"), "Unknown speaker")


def _broadcaster_name(item: dict) -> str:
    return _safe_text(item.get("broadcaster", {}).get("displayName"), "Unknown broadcaster")


class WorkerSignals(QObject):
    started = Signal()
    status = Signal(str)
    progress = Signal(int, int)
    finished = Signal(object)
    error = Signal(str)
    done = Signal()


class Worker(QRunnable):
    def __init__(self, fn: Callable[[WorkerSignals], Any]):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    def _emit(self, signal: Signal, *args):
        try:
            signal.emit(*args)
        except RuntimeError:
            # Receiver/source can be destroyed during app shutdown.
            pass

    def run(self):
        self._emit(self.signals.started)
        try:
            result = self.fn(self.signals)
        except Exception as exc:
            self._emit(self.signals.error, f"{exc}\n{traceback.format_exc()}")
        else:
            self._emit(self.signals.finished, result)
        finally:
            self._emit(self.signals.done)


class GuiLogStream(QObject):
    text_emitted = Signal(str)

    def __init__(self, original):
        super().__init__()
        self.original = original

    def write(self, s):
        if not s:
            return 0
        self.text_emitted.emit(str(s))
        try:
            self.original.write(str(s))
            self.original.flush()
        except Exception:
            pass
        return len(s)

    def flush(self):
        try:
            self.original.flush()
        except Exception:
            pass


@dataclass
class QueueTaskUI:
    task_id: int
    name_item: QTableWidgetItem
    type_item: QTableWidgetItem
    status_item: QTableWidgetItem
    progress_bar: QProgressBar
    action_btn: QPushButton
    open_path: Optional[str] = None
    done: bool = False


class CollapsibleSection(QFrame):
    def __init__(self, title: str, start_collapsed: bool = False):
        super().__init__()
        self.setObjectName("CollapsibleSection")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        self.toggle_btn = QToolButton()
        self.toggle_btn.setObjectName("CollapseToggle")
        self.toggle_btn.setText(title)
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(not start_collapsed)
        self.toggle_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_btn.setArrowType(
            Qt.ArrowType.DownArrow if not start_collapsed else Qt.ArrowType.RightArrow
        )
        self.toggle_btn.clicked.connect(self._on_toggled)

        self.body = QWidget()
        self.body.setVisible(not start_collapsed)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(6)

        outer.addWidget(self.toggle_btn)
        outer.addWidget(self.body)

    def _on_toggled(self, checked: bool):
        self.body.setVisible(bool(checked))
        self.toggle_btn.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )

    def clear_content(self):
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()


class SermonAudioMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SermonAudio Downloader")
        self.resize(1200, 860)
        self.setFont(QFont("Segoe UI", 9))

        self.config = sa_config.load_config()
        self.download_dir = sa_config.download_dir(self.config)
        self.show_logs = bool(self.config.get("show_logs", True))
        self.dark_mode = self.config.get("theme_mode", "dark") == "dark"

        self.thread_pool = QThreadPool.globalInstance()
        self.queue_tasks: dict[int, QueueTaskUI] = {}
        self.next_task_id = 1
        self.active_tasks = 0

        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self.log_stream = GuiLogStream(sys.__stdout__)
        self.log_stream.text_emitted.connect(self._append_log_text)
        sys.stdout = self.log_stream
        sys.stderr = self.log_stream

        self._build_ui()
        self._apply_theme(self.dark_mode)
        self._update_log_visibility(self.show_logs, save=False)
        self._refresh_queue_counts()
        self._refresh_auth_status(force=False)

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("AppRoot")
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        header = self._build_header()
        self._apply_shadow(header, blur=36, y_offset=6, alpha=90)
        root.addWidget(header)

        folder = self._build_folder_bar()
        self._apply_shadow(folder, blur=22, y_offset=4, alpha=70)
        root.addWidget(folder)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_search_tab(), "Search")
        self.tabs.addTab(self._build_manual_tab(), "Manual Download")
        self.tabs.addTab(self._build_queue_tab(), "Queue")
        tabs_shell = QFrame()
        tabs_shell.setObjectName("TabsShell")
        tabs_layout = QVBoxLayout(tabs_shell)
        tabs_layout.setContentsMargins(10, 10, 10, 10)
        tabs_layout.addWidget(self.tabs)
        self._apply_shadow(tabs_shell, blur=26, y_offset=5, alpha=75)
        root.addWidget(tabs_shell, 1)

        log_shell = self._build_log_panel()
        log_shell.setObjectName("LogShell")
        self._apply_shadow(log_shell, blur=18, y_offset=4, alpha=65)
        root.addWidget(log_shell)

        self.setCentralWidget(central)

    def _apply_shadow(self, widget: QWidget, blur: int, y_offset: int, alpha: int):
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(blur)
        shadow.setOffset(0, y_offset)
        shadow.setColor(QColor(0, 0, 0, alpha))
        widget.setGraphicsEffect(shadow)

    def _build_header(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("HeaderFrame")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 16)

        left = QVBoxLayout()
        title = QLabel("SermonAudio Downloader")
        title.setObjectName("HeaderTitle")
        subtitle = QLabel("Qt desktop app with live search, queue workers, and progress tracking.")
        subtitle.setObjectName("HeaderSubtitle")
        left.addWidget(title)
        left.addWidget(subtitle)
        layout.addLayout(left, 1)

        right = QHBoxLayout()
        self.auth_label = QLabel("Checking API key...")
        self.auth_label.setObjectName("AuthLabel")
        self.settings_btn = QToolButton()
        self.settings_btn.setText("Settings")
        self.settings_btn.clicked.connect(self._open_settings_dialog)
        right.addWidget(self.auth_label)
        right.addWidget(self.settings_btn)
        layout.addLayout(right)

        return frame

    def _build_folder_bar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("FolderBar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Download folder:"))
        self.download_dir_label = QLabel(self.download_dir)
        self.download_dir_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.download_dir_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.download_dir_label, 1)

        layout.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        for media, quality, label, _note in FORMAT_MENU:
            self.format_combo.addItem(label, f"{media}:{quality}")
        idx = self.format_combo.findData(self.config.get("gui_format", "audio:low"))
        self.format_combo.setCurrentIndex(max(0, idx))
        self.format_combo.currentIndexChanged.connect(
            lambda _i: sa_config.save_config("gui_format", self.format_combo.currentData()))
        layout.addWidget(self.format_combo)

        change_btn = QPushButton("Change")
        change_btn.clicked.connect(self._choose_download_dir)
        open_btn = QPushButton("Open")
        open_btn.clicked.connect(lambda: self._open_path(self.download_dir))
        layout.addWidget(change_btn)
        layout.addWidget(open_btn)
        return frame

    def _build_search_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.setContentsMargins(2, 2, 2, 2)
        top.setSpacing(10)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search sermons, speakers, broadcasters...")
        self.search_input.returnPressed.connect(self._run_search)
        self.search_newest_check = QCheckBox("Newest")
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._run_search)
        top.addWidget(self.search_input, 1)
        top.addWidget(self.search_newest_check)
        top.addWidget(self.search_btn)
        layout.addLayout(top)

        self.search_status = QLabel("")
        self.search_status.setObjectName("SearchStatus")
        layout.addWidget(self.search_status)

        self.search_scroll = QScrollArea()
        self.search_scroll.setObjectName("SearchScroll")
        self.search_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.search_scroll.setWidgetResizable(True)
        self.search_scroll.viewport().setObjectName("SearchViewport")

        self.search_results_host = QWidget()
        self.search_results_host.setObjectName("SearchResultsHost")
        self.search_results_layout = QVBoxLayout(self.search_results_host)
        self.search_results_layout.setContentsMargins(6, 6, 6, 6)
        self.search_results_layout.setSpacing(8)

        self.speakers_section = CollapsibleSection("Speakers", start_collapsed=True)
        self.sermons_section = CollapsibleSection("Sermons", start_collapsed=True)
        self.series_section = CollapsibleSection("Serieses", start_collapsed=True)
        self.broadcasters_section = CollapsibleSection("Broadcasters", start_collapsed=True)

        self.search_results_layout.addWidget(self.speakers_section)
        self.search_results_layout.addWidget(self.sermons_section)
        self.search_results_layout.addWidget(self.series_section)
        self.search_results_layout.addWidget(self.broadcasters_section)
        self.search_results_layout.addStretch(1)

        self.search_scroll.setWidget(self.search_results_host)
        layout.addWidget(self.search_scroll, 1)

        return tab

    def _build_manual_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.manual_target_input = QLineEdit()
        self.manual_target_input.setPlaceholderText("Sermon URL or ID")
        self.manual_target_input.returnPressed.connect(self._queue_manual_download)


        queue_btn = QPushButton("Queue Download")
        queue_btn.clicked.connect(self._queue_manual_download)

        row.addWidget(self.manual_target_input, 1)
        row.addWidget(queue_btn)
        layout.addLayout(row)

        help_lbl = QLabel("Use a sermon page URL, direct media URL, or numeric sermon ID.")
        help_lbl.setObjectName("MutedText")
        layout.addWidget(help_lbl)
        layout.addStretch(1)
        return tab

    def _build_queue_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        queue_lbl = QLabel("Queue")
        queue_lbl.setObjectName("PanelLabel")
        top.addWidget(queue_lbl)
        self.queued_count_label = QLabel("0 queued")
        self.active_count_label = QLabel("0 active")
        top.addWidget(self.queued_count_label)
        top.addWidget(self.active_count_label)
        top.addStretch(1)
        clear_done_btn = QPushButton("Clear Completed")
        clear_done_btn.clicked.connect(self._clear_completed_tasks)
        clear_all_btn = QPushButton("Clear All")
        clear_all_btn.clicked.connect(self._clear_all_tasks)
        top.addWidget(clear_done_btn)
        top.addWidget(clear_all_btn)
        layout.addLayout(top)

        self.queue_table = QTableWidget(0, 5)
        self.queue_table.setHorizontalHeaderLabels(["Item", "Type", "Status", "Progress", "Action"])
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.queue_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.queue_table.setAlternatingRowColors(True)
        self.queue_table.setShowGrid(False)
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.queue_table, 1)
        return tab

    def _build_log_panel(self) -> QWidget:
        frame = QFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setContentsMargins(8, 4, 8, 6)
        row.setSpacing(10)
        logs_lbl = QLabel("Logs")
        logs_lbl.setObjectName("PanelLabel")
        row.addWidget(logs_lbl)
        row.addStretch(1)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self.log_text.clear())
        self.toggle_logs_btn = QPushButton("Hide Logs")
        self.toggle_logs_btn.clicked.connect(self._toggle_logs_visibility)
        row.addWidget(clear_btn)
        row.addWidget(self.toggle_logs_btn)
        layout.addLayout(row)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("LogText")
        self.log_text.setFrameShape(QFrame.Shape.NoFrame)
        self.log_text.viewport().setObjectName("LogViewport")
        self.log_text.setMinimumHeight(140)
        layout.addWidget(self.log_text)
        return frame
    def _apply_theme(self, dark: bool):
        if dark:
            self.setStyleSheet(
                """
                QMainWindow { background: #081018; color: #e7edf3; }
                QWidget#AppRoot { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #091522, stop:0.55 #0c1724, stop:1 #10131f); }
                QLabel { color: #deebf4; background: transparent; }
                QFrame#HeaderFrame {
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #0d6e86, stop:0.55 #0d8da2, stop:1 #117f94);
                    border: 1px solid #2ca4b7;
                    border-radius: 16px;
                }
                QLabel#HeaderTitle { color: #ffffff; font-size: 30px; font-weight: 800; letter-spacing: 0.25px; }
                QLabel#HeaderSubtitle { color: #d9f5fa; font-size: 13px; }
                QLabel#AuthLabel { color: #c0ffd0; font-weight: 700; }
                QLabel#PanelLabel { color: #dff0fb; font-size: 22px; font-weight: 700; }

                QFrame#FolderBar, QFrame#TabsShell, QFrame#LogShell {
                    border: 1px solid #263748;
                    border-radius: 12px;
                    background: #101a25;
                }

                QLabel#SearchStatus, QLabel#MutedText { color: #94aeca; }
                QFrame#ResultCard {
                    border: 1px solid #2a3f54;
                    border-radius: 12px;
                    background: #131f2d;
                }
                QLabel#ResultTitle { color: #eaf6ff; font-size: 12px; font-weight: 700; }
                QLabel#ResultMeta { color: #9eb8cc; font-size: 10px; }
                QFrame#CollapsibleSection {
                    border: 1px solid #2a3f54;
                    border-radius: 8px;
                    background: #111c29;
                    padding: 2px;
                }
                QToolButton#CollapseToggle {
                    text-align: left;
                    border: 1px solid #2e4459;
                    border-radius: 8px;
                    background: #162737;
                    color: #b9d3e7;
                    font-weight: 600;
                    padding: 5px 10px;
                }
                QToolButton#CollapseToggle:hover {
                    background: #1f3348;
                    border-color: #4e7494;
                }
                QToolButton#CollapseToggle:checked {
                    background: #20506d;
                    color: #f3fbff;
                    border-color: #3f6b89;
                }

                QLineEdit, QComboBox, QTableWidget {
                    background: #0f1a26;
                    border: 1px solid #2d4256;
                    border-radius: 7px;
                    padding: 5px;
                    color: #e5f2ff;
                    selection-background-color: #2f94ae;
                }
                QLineEdit::placeholder {
                    color: #7f9fb8;
                }
                QComboBox QAbstractItemView {
                    background: #0f1a26;
                    color: #e5f2ff;
                    border: 1px solid #2d4256;
                    selection-background-color: #2f94ae;
                    selection-color: #f5fcff;
                }
                QCheckBox {
                    color: #dcebf7;
                    spacing: 8px;
                    padding-right: 2px;
                }
                QCheckBox::indicator {
                    width: 16px;
                    height: 16px;
                    border: 1px solid #4f6f89;
                    border-radius: 4px;
                    background: #0f1a26;
                }
                QCheckBox::indicator:hover {
                    border-color: #6f92ae;
                }
                QCheckBox::indicator:checked {
                    background: #2f94ae;
                    border-color: #68cbde;
                }
                QScrollArea#SearchScroll {
                    border: 1px solid #2a3f54;
                    border-radius: 10px;
                    background: #0f1a26;
                }
                QWidget#SearchViewport, QWidget#SearchResultsHost {
                    background: #0f1a26;
                }
                QTextEdit#LogText {
                    background: #0b141e;
                    border: 1px solid #334b62;
                    border-radius: 10px;
                    color: #d8edf8;
                    padding: 8px;
                }
                QWidget#LogViewport {
                    background: #0b141e;
                    color: #d8edf8;
                }
                QHeaderView::section {
                    background: #152435;
                    color: #d7eaf6;
                    border: none;
                    border-bottom: 1px solid #2f455a;
                    padding: 6px 8px;
                    font-weight: 600;
                }
                QTableWidget {
                    alternate-background-color: #0f1b28;
                }

                QProgressBar {
                    background: #0b141e;
                    border: 1px solid #334b62;
                    border-radius: 7px;
                    text-align: center;
                    color: #d8edf8;
                }
                QProgressBar::chunk {
                    border-radius: 6px;
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #17a3bf, stop:1 #39d0e6);
                }

                QPushButton, QToolButton {
                    background: #203447;
                    border: 1px solid #38556d;
                    border-radius: 7px;
                    padding: 6px 11px;
                    color: #dcedf7;
                    font-weight: 600;
                }
                QPushButton#ResultActionButton {
                    padding: 4px 9px;
                    border-radius: 6px;
                }
                QPushButton:hover, QToolButton:hover { background: #29445b; border-color: #4e7494; }
                QPushButton:pressed, QToolButton:pressed { background: #1a2c3a; }
                QPushButton:disabled { color: #8298aa; background: #16222f; border-color: #253443; }

                QTabWidget::pane {
                    border: 1px solid #2b4258;
                    border-radius: 10px;
                    top: -2px;
                    background: #0f1a26;
                }
                QTabBar::tab {
                    background: #162737;
                    border: 1px solid #2e4459;
                    border-bottom: none;
                    padding: 8px 14px;
                    border-top-left-radius: 8px;
                    border-top-right-radius: 8px;
                    margin-right: 4px;
                    color: #b9d3e7;
                }
                QTabBar::tab:selected {
                    background: #20506d;
                    color: #f3fbff;
                    border-color: #3f6b89;
                }
                """
            )
        else:
            self.setStyleSheet(
                """
                QMainWindow { background: #f3f8fd; color: #1a2b39; }
                QWidget#AppRoot { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #eef5fb, stop:0.55 #f6fbff, stop:1 #eaf3fb); }
                QLabel { color: #1f3448; background: transparent; }
                QFrame#HeaderFrame {
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #197b96, stop:0.55 #2f9fb7, stop:1 #3ba7bd);
                    border: 1px solid #69bfd0;
                    border-radius: 16px;
                }
                QLabel#HeaderTitle { color: #ffffff; font-size: 30px; font-weight: 800; letter-spacing: 0.25px; }
                QLabel#HeaderSubtitle { color: #eaffff; font-size: 13px; }
                QLabel#AuthLabel { color: #106930; font-weight: 700; }
                QLabel#PanelLabel { color: #1d3b50; font-size: 22px; font-weight: 700; }

                QFrame#FolderBar, QFrame#TabsShell, QFrame#LogShell {
                    border: 1px solid #cad8e4;
                    border-radius: 12px;
                    background: #ffffff;
                }

                QLabel#SearchStatus, QLabel#MutedText { color: #5a748d; }
                QFrame#ResultCard {
                    border: 1px solid #d2e0ec;
                    border-radius: 12px;
                    background: #f9fcff;
                }
                QLabel#ResultTitle { color: #1f3c50; font-size: 12px; font-weight: 700; }
                QLabel#ResultMeta { color: #56738a; font-size: 10px; }
                QFrame#CollapsibleSection {
                    border: 1px solid #d2e0ec;
                    border-radius: 8px;
                    background: #f7fbff;
                    padding: 2px;
                }
                QToolButton#CollapseToggle {
                    text-align: left;
                    border: 1px solid #c7d7e4;
                    border-radius: 8px;
                    background: #e8f0f7;
                    color: #445d71;
                    font-weight: 600;
                    padding: 5px 10px;
                }
                QToolButton#CollapseToggle:hover {
                    background: #dceaf5;
                    border-color: #9fb7c8;
                }
                QToolButton#CollapseToggle:checked {
                    background: #ffffff;
                    color: #12344a;
                    border-color: #a8c2d4;
                }

                QLineEdit, QComboBox, QTableWidget {
                    background: #ffffff;
                    border: 1px solid #c7d7e4;
                    border-radius: 7px;
                    padding: 5px;
                    color: #22394d;
                    selection-background-color: #6ebbd0;
                }
                QLineEdit::placeholder {
                    color: #8399ac;
                }
                QComboBox QAbstractItemView {
                    background: #ffffff;
                    color: #22394d;
                    border: 1px solid #c7d7e4;
                    selection-background-color: #6ebbd0;
                    selection-color: #12384e;
                }
                QCheckBox {
                    color: #27465b;
                    spacing: 8px;
                    padding-right: 2px;
                }
                QCheckBox::indicator {
                    width: 16px;
                    height: 16px;
                    border: 1px solid #9fb8ca;
                    border-radius: 4px;
                    background: #ffffff;
                }
                QCheckBox::indicator:hover {
                    border-color: #7ea0b8;
                }
                QCheckBox::indicator:checked {
                    background: #52b9cf;
                    border-color: #2d92aa;
                }
                QScrollArea#SearchScroll {
                    border: 1px solid #cfdeea;
                    border-radius: 10px;
                    background: #ffffff;
                }
                QWidget#SearchViewport, QWidget#SearchResultsHost {
                    background: #ffffff;
                }
                QTextEdit#LogText {
                    background: #ffffff;
                    border: 1px solid #ccd9e3;
                    border-radius: 10px;
                    color: #244257;
                    padding: 8px;
                }
                QWidget#LogViewport {
                    background: #ffffff;
                    color: #244257;
                }
                QHeaderView::section {
                    background: #e9f1f8;
                    color: #203748;
                    border: none;
                    border-bottom: 1px solid #c8d9e5;
                    padding: 6px 8px;
                    font-weight: 600;
                }
                QTableWidget {
                    alternate-background-color: #f5faff;
                }

                QProgressBar {
                    background: #edf4fb;
                    border: 1px solid #c7d8e6;
                    border-radius: 7px;
                    text-align: center;
                    color: #244257;
                }
                QProgressBar::chunk {
                    border-radius: 6px;
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #2da5be, stop:1 #58cbe0);
                }

                QPushButton, QToolButton {
                    background: #e7f0f8;
                    border: 1px solid #bfd1de;
                    border-radius: 7px;
                    padding: 6px 11px;
                    color: #1f3547;
                    font-weight: 600;
                }
                QPushButton#ResultActionButton {
                    padding: 4px 9px;
                    border-radius: 6px;
                }
                QPushButton:hover, QToolButton:hover { background: #dceaf5; border-color: #9fb7c8; }
                QPushButton:pressed, QToolButton:pressed { background: #d0e2ef; }
                QPushButton:disabled { color: #7f95a7; background: #ecf3f9; border-color: #d3e0ea; }

                QTabWidget::pane {
                    border: 1px solid #d0dee9;
                    border-radius: 10px;
                    top: -2px;
                    background: #ffffff;
                }
                QTabBar::tab {
                    background: #e8f0f7;
                    border: 1px solid #c7d7e4;
                    border-bottom: none;
                    padding: 8px 14px;
                    border-top-left-radius: 8px;
                    border-top-right-radius: 8px;
                    margin-right: 4px;
                    color: #445d71;
                }
                QTabBar::tab:selected {
                    background: #ffffff;
                    color: #12344a;
                    border-color: #a8c2d4;
                }
                """
            )

    def _append_log_text(self, text: str):
        if not text:
            return
        self.log_text.moveCursor(QTextCursor.MoveOperation.End)
        self.log_text.insertPlainText(text)
        self.log_text.moveCursor(QTextCursor.MoveOperation.End)
        plain = self.log_text.toPlainText()
        if len(plain) > 120000:
            self.log_text.setPlainText(plain[-90000:])
            self.log_text.moveCursor(QTextCursor.MoveOperation.End)

    def _toggle_logs_visibility(self):
        self._update_log_visibility(not self.log_text.isVisible(), save=True)

    def _update_log_visibility(self, visible: bool, save: bool):
        self.log_text.setVisible(visible)
        self.toggle_logs_btn.setText("Hide Logs" if visible else "Show Logs")
        self.show_logs = visible
        if save:
            sa_config.save_config("show_logs", visible)

    def _show_message(self, title: str, message: str):
        QMessageBox.information(self, title, message)

    def _show_error(self, title: str, message: str):
        QMessageBox.critical(self, title, message)

    def _set_download_dir(self, folder: str):
        self.download_dir = folder
        self.download_dir_label.setText(folder)
        sa_config.save_config("download_dir", folder)
        self._show_message("Download Folder", "Download folder updated.")

    def _choose_download_dir(self):
        chosen = QFileDialog.getExistingDirectory(self, "Choose download folder", self.download_dir)
        if chosen:
            self._set_download_dir(chosen)

    def _open_path(self, path: str):
        if not path:
            return
        try:
            if os.path.exists(path):
                if sys.platform == "win32":
                    os.startfile(path)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.call(("open", path))
                else:
                    subprocess.call(("xdg-open", path))
                return
            QDesktopServices.openUrl(path)
        except Exception as exc:
            print(f"[GUI] Failed to open path/url: {exc}")
            self._show_error("Open Error", str(exc))

    def _refresh_auth_status(self, force: bool):
        def _job(signals: WorkerSignals):
            key = api.get_api_key(force_refresh=force)
            return key

        worker = Worker(_job)
        worker.signals.finished.connect(self._on_auth_success)
        worker.signals.error.connect(self._on_auth_error)
        self.thread_pool.start(worker)

    def _on_auth_success(self, key: str):
        short = _safe_text(key)[-6:]
        self.auth_label.setText(f"API key active (...{short})")

    def _on_auth_error(self, err: str):
        self.auth_label.setText("Auth error")
        print(err)

    def _run_search(self):
        query = _safe_text(self.search_input.text())
        if not query:
            self._show_message("Search", "Enter a search query first.")
            return

        newest = self.search_newest_check.isChecked()
        self.search_btn.setEnabled(False)
        self.search_status.setText("Searching...")

        def _job(signals: WorkerSignals):
            sort_by = "newest-published" if newest else None
            data = api.search(query, sort_by=sort_by)
            return data

        worker = Worker(_job)
        worker.signals.finished.connect(self._render_search_results)
        worker.signals.error.connect(self._on_search_error)
        worker.signals.done.connect(lambda: self.search_btn.setEnabled(True))
        self.thread_pool.start(worker)

    def _clear_search_results(self):
        for section in (
            self.speakers_section,
            self.broadcasters_section,
            self.sermons_section,
            self.series_section,
        ):
            section.clear_content()

    def _add_empty_result_label(self, section: CollapsibleSection, text: str):
        label = QLabel(text)
        label.setObjectName("MutedText")
        label.setWordWrap(True)
        section.body_layout.addWidget(label)

    def _render_search_results(self, data: dict):
        self._clear_search_results()
        total = 0

        speakers = (data.get("speakerResults") or [])[:10]
        if speakers:
            total += len(speakers)
            for item in speakers:
                sid = _safe_text(item.get("speakerID"))
                name = _safe_text(item.get("displayName"), "Unknown speaker")
                count = item.get("sermonCount")
                count_text = f"{count} sermons" if count is not None else "Unknown sermon count"
                self.speakers_section.body_layout.addWidget(
                    self._make_result_card(
                        title=name,
                        subtitle=f"Speaker ID: {sid} | {count_text}",
                        queue_cb=lambda _=False, target=sid, n=name: self._queue_bulk_download("speaker", target, n),
                        open_cb=None,
                        queue_label="Queue All",
                    )
                )
        else:
            self._add_empty_result_label(self.speakers_section, "No speaker results.")

        broadcasters = (data.get("broadcasterResults") or [])[:8]
        if broadcasters:
            total += len(broadcasters)
            for item in broadcasters:
                bid = _safe_text(item.get("broadcasterID"))
                name = _safe_text(item.get("displayName"), "Unknown broadcaster")
                location = _safe_text(item.get("location"), "Unknown location")
                self.broadcasters_section.body_layout.addWidget(
                    self._make_result_card(
                        title=name,
                        subtitle=f"{location} | Broadcaster ID: {bid}",
                        queue_cb=lambda _=False, target=bid, n=name: self._queue_bulk_download("broadcaster", target, n),
                        open_cb=lambda _=False, b=bid: webbrowser.open(f"https://www.sermonaudio.com/broadcasters/{b}"),
                        queue_label="Queue All",
                    )
                )
        else:
            self._add_empty_result_label(self.broadcasters_section, "No broadcaster results.")

        sermons = (data.get("sermonResults") or [])[:12]
        if sermons:
            total += len(sermons)
            for item in sermons:
                sid = str(item.get("sermonID", ""))
                title = _sermon_title(item)
                speaker = _speaker_name(item)
                broadcaster = _broadcaster_name(item)
                date = _safe_text(item.get("preachDate"), "Unknown date")
                url = f"https://www.sermonaudio.com/sermons/{sid}"
                self.sermons_section.body_layout.addWidget(
                    self._make_result_card(
                        title=title,
                        subtitle=f"{speaker} | {broadcaster} | {date} | ID: {sid}",
                        queue_cb=lambda _=False, target=sid, t=title: self._queue_single_download(target, title=t),
                        open_cb=lambda _=False, u=url: webbrowser.open(u),
                        queue_label="Queue",
                    )
                )
        else:
            self._add_empty_result_label(self.sermons_section, "No sermon results.")

        series_items = (data.get("seriesResults") or [])[:10]
        if series_items:
            total += len(series_items)
            for item in series_items:
                series_id = _safe_text(item.get("seriesID"))
                title = _safe_text(item.get("title"), "Untitled series")
                broadcaster = _safe_text(item.get("broadcaster", {}).get("displayName"), "Unknown broadcaster")
                sermon_count = item.get("count")
                sermon_count_text = f"{sermon_count} sermons" if sermon_count is not None else "Unknown count"
                latest = _safe_text(item.get("latest"), "Unknown latest date")
                url = f"https://www.sermonaudio.com/series/{series_id}"
                self.series_section.body_layout.addWidget(
                    self._make_result_card(
                        title=title,
                        subtitle=f"{broadcaster} | {sermon_count_text} | Latest: {latest} | ID: {series_id}",
                        queue_cb=lambda _=False, target=series_id, n=title: self._queue_bulk_download("series", target, n),
                        open_cb=lambda _=False, u=url: webbrowser.open(u),
                        queue_label="Queue All",
                    )
                )
        else:
            self._add_empty_result_label(self.series_section, "No series results.")

        if total == 0:
            self.search_status.setText("No results.")
        else:
            self.search_status.setText(f"Loaded {total} result items.")

    def _on_search_error(self, err: str):
        self.search_status.setText("Search failed.")
        print(err)
        self._show_error("Search Error", "Search failed. Check logs for details.")

    def _make_result_card(
        self,
        title: str,
        subtitle: str,
        queue_cb: Optional[Callable],
        open_cb: Optional[Callable],
        queue_label: Optional[str],
    ) -> QWidget:
        frame = QFrame()
        frame.setObjectName("ResultCard")
        frame.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("ResultTitle")
        title_font = QFont()
        title_font.setPointSize(9)
        title_font.setBold(True)
        title_lbl.setFont(title_font)
        title_lbl.setWordWrap(True)

        sub_lbl = QLabel(subtitle)
        sub_lbl.setWordWrap(True)
        sub_lbl.setObjectName("ResultMeta")
        sub_font = QFont()
        sub_font.setPointSize(8)
        sub_lbl.setFont(sub_font)

        btn_row = QHBoxLayout()
        if queue_cb and queue_label:
            queue_btn = QPushButton(queue_label)
            queue_btn.setObjectName("ResultActionButton")
            queue_btn.clicked.connect(queue_cb)
            btn_row.addWidget(queue_btn)

        if open_cb:
            open_btn = QPushButton("Open")
            open_btn.setObjectName("ResultActionButton")
            open_btn.clicked.connect(open_cb)
            btn_row.addWidget(open_btn)

        btn_row.addStretch(1)

        layout.addWidget(title_lbl)
        layout.addWidget(sub_lbl)
        layout.addLayout(btn_row)
        return frame

    def _current_format(self) -> tuple[str, str]:
        media, _, quality = (self.format_combo.currentData() or "audio:low").partition(":")
        return media, quality

    def _queue_manual_download(self):
        target = _safe_text(self.manual_target_input.text())
        if not target:
            self._show_message("Manual Download", "Enter a sermon URL or ID first.")
            return
        self._queue_single_download(target=target, title=target)

    def _new_task_id(self) -> int:
        task_id = self.next_task_id
        self.next_task_id += 1
        return task_id

    def _add_queue_task_row(self, item_name: str, item_type: str, status: str = "Pending") -> int:
        task_id = self._new_task_id()
        row = self.queue_table.rowCount()
        self.queue_table.insertRow(row)

        name_item = QTableWidgetItem(item_name)
        type_item = QTableWidgetItem(item_type)
        status_item = QTableWidgetItem(status)

        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setValue(0)

        action_btn = QPushButton("Open")
        action_btn.setEnabled(False)
        action_btn.clicked.connect(lambda _=False, tid=task_id: self._open_task_output(tid))

        self.queue_table.setItem(row, 0, name_item)
        self.queue_table.setItem(row, 1, type_item)
        self.queue_table.setItem(row, 2, status_item)
        self.queue_table.setCellWidget(row, 3, progress)
        self.queue_table.setCellWidget(row, 4, action_btn)

        self.queue_tasks[task_id] = QueueTaskUI(
            task_id=task_id,
            name_item=name_item,
            type_item=type_item,
            status_item=status_item,
            progress_bar=progress,
            action_btn=action_btn,
        )
        self._refresh_queue_counts()
        return task_id

    def _refresh_queue_counts(self):
        self.queued_count_label.setText(f"{len(self.queue_tasks)} queued")
        self.active_count_label.setText(f"{self.active_tasks} active")

    def _change_active_tasks(self, delta: int):
        self.active_tasks = max(0, self.active_tasks + delta)
        self._refresh_queue_counts()

    def _set_task_status(self, task_id: int, text: str):
        task = self.queue_tasks.get(task_id)
        if not task:
            return
        task.status_item.setText(text)

    def _set_task_progress(self, task_id: int, current: int, total: int):
        task = self.queue_tasks.get(task_id)
        if not task:
            return
        if total <= 0:
            task.progress_bar.setRange(0, 0)
            return

        task.progress_bar.setRange(0, 100)
        pct = int((max(0, current) / max(1, total)) * 100)
        task.progress_bar.setValue(min(100, pct))

    def _mark_task_error(self, task_id: int, err: str):
        task = self.queue_tasks.get(task_id)
        if not task:
            return
        task.done = True
        task.status_item.setText("Error")
        task.status_item.setForeground(QColor("#d9534f"))
        task.progress_bar.setRange(0, 100)
        print(err)

    def _mark_task_finished(self, task_id: int, result: Any):
        task = self.queue_tasks.get(task_id)
        if not task:
            return
        task.done = True
        task.progress_bar.setRange(0, 100)
        task.progress_bar.setValue(100)

        status = "Complete"
        open_path = None
        if isinstance(result, dict):
            status = _safe_text(result.get("status"), "Complete")
            open_path = result.get("open_path")
        elif isinstance(result, str):
            open_path = result

        task.status_item.setText(status)
        task.status_item.setForeground(QColor("#41a65a"))
        if open_path and os.path.exists(open_path):
            task.open_path = open_path
            task.action_btn.setEnabled(True)

    def _open_task_output(self, task_id: int):
        task = self.queue_tasks.get(task_id)
        if not task or not task.open_path:
            return
        self._open_path(task.open_path)

    def _remove_task_row(self, task_id: int):
        task = self.queue_tasks.get(task_id)
        if not task:
            return
        row = self.queue_table.row(task.name_item)
        if row >= 0:
            self.queue_table.removeRow(row)
        self.queue_tasks.pop(task_id, None)
        self._refresh_queue_counts()

    def _clear_completed_tasks(self):
        to_remove = [tid for tid, task in self.queue_tasks.items() if task.done]
        for tid in to_remove:
            self._remove_task_row(tid)

    def _clear_all_tasks(self):
        if self.active_tasks > 0:
            self._show_message("Queue", "Active tasks are still running. Wait for completion first.")
            return
        for tid in list(self.queue_tasks.keys()):
            self._remove_task_row(tid)
    def _queue_single_download(
        self,
        target: str,
        title: Optional[str] = None,
        media_format: Optional[str] = None,
        quality: Optional[str] = None,
    ):
        item_title = title or target
        fmt, qual = self._current_format()
        fmt = media_format or fmt
        qual = normalize_quality(fmt, quality or qual)

        task_id = self._add_queue_task_row(item_name=item_title, item_type=f"Single ({fmt})", status="Queued")
        self.tabs.setCurrentIndex(2)

        def _job(signals: WorkerSignals):
            signals.status.emit("Preparing")
            t = resolve(target, "sermon")
            if t.kind != "sermon":
                raise RuntimeError("Manual download takes a single sermon link or ID.")
            media = t.media or fmt
            quality = normalize_quality(media, t.quality if t.media else qual)
            sermon = single_sermon(t.value)
            if media == "video" and sermon.has_video is False:
                raise RuntimeError("This sermon has no video available.")

            def _progress(current, total):
                signals.progress.emit(int(current), int(total))
                signals.status.emit(f"{int(current * 100 / total)}%" if total else "Downloading")

            status, output = download_sermon(sermon, self.download_dir, media, quality, progress=_progress)
            if status == "missing" and media == "video":
                raise RuntimeError("No video is available for this sermon.")
            if status == "missing":
                raise RuntimeError(f"No {media} file is available for this sermon.")
            return {"status": "Already had" if status == "skipped" else "Complete", "open_path": str(output)}

        self._start_queue_worker(task_id, _job)

    def _queue_bulk_download(self, mode: str, target_id: str, name_hint: str):
        mode = mode.lower().strip()
        if mode not in ("speaker", "broadcaster", "series"):
            self._show_error("Queue Error", f"Unknown bulk mode: {mode}")
            return

        fmt, qual = self._current_format()
        task_id = self._add_queue_task_row(item_name=name_hint, item_type=f"Bulk ({mode}, {fmt})", status="Queued")
        self.tabs.setCurrentIndex(2)

        def _job(signals: WorkerSignals):
            signals.status.emit("Fetching list")
            signals.progress.emit(0, 0)

            if mode == "speaker":
                sermons = speaker_sermons(target_id)
                display_name = sermons[0].speaker if sermons else name_hint
            elif mode == "broadcaster":
                sermons = broadcaster_sermons(target_id)
                display_name = sermons[0].broadcaster if sermons else name_hint
            else:
                display_name, sermons = series_sermons(resolve(target_id, "series").value)

            if not sermons:
                raise RuntimeError("No sermons found for this target.")

            def _on_done(idx, total, sermon, status, path):
                signals.status.emit(f"{idx}/{total}")
                signals.progress.emit(idx, total)
                if status.startswith("error") or status == "missing":
                    print(f"[bulk] {sermon.sermon_id} {sermon.title}: {status}")

            workers = int(sa_config.load_config().get("workers", 3))
            if fmt == "video":
                sermons = [s for s in sermons if s.has_video is not False]
                if not sermons:
                    raise RuntimeError("None of these sermons have video.")
            counts = download_many(sermons, self.download_dir, fmt, qual, workers=workers, on_done=_on_done)
            failed = counts["error"] + counts["missing"]
            status = "Complete" if failed == 0 else f"Complete ({failed} failed)"
            first = sermons[0]
            folder = os.path.join(self.download_dir, clean(first.folder or first.speaker or first.broadcaster,
                                                           "Unknown Speaker"))
            return {"status": status, "open_path": folder}

        self._start_queue_worker(task_id, _job)

    def _start_queue_worker(self, task_id: int, job_fn: Callable[[WorkerSignals], Any]):
        worker = Worker(job_fn)
        worker.signals.started.connect(lambda tid=task_id: self._change_active_tasks(1))
        worker.signals.status.connect(lambda text, tid=task_id: self._set_task_status(tid, text))
        worker.signals.progress.connect(lambda cur, tot, tid=task_id: self._set_task_progress(tid, cur, tot))
        worker.signals.error.connect(lambda err, tid=task_id: self._mark_task_error(tid, err))
        worker.signals.finished.connect(lambda result, tid=task_id: self._mark_task_finished(tid, result))
        worker.signals.done.connect(lambda tid=task_id: self._change_active_tasks(-1))
        self.thread_pool.start(worker)

    def _open_settings_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        dialog.resize(620, 280)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)

        dark_check = QCheckBox("Dark mode")
        dark_check.setChecked(self.dark_mode)
        dark_check.toggled.connect(self._set_dark_mode)

        logs_check = QCheckBox("Show logs")
        logs_check.setChecked(self.show_logs)
        logs_check.toggled.connect(lambda v: self._update_log_visibility(v, save=True))

        layout.addWidget(dark_check)
        layout.addWidget(logs_check)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Download folder:"))
        folder_edit = QLineEdit(self.download_dir)
        folder_edit.setReadOnly(True)
        folder_row.addWidget(folder_edit, 1)
        folder_choose_btn = QPushButton("Change Folder")

        def _choose_folder_dialog():
            chosen = QFileDialog.getExistingDirectory(dialog, "Choose download folder", self.download_dir)
            if chosen:
                self._set_download_dir(chosen)
                folder_edit.setText(chosen)

        folder_choose_btn.clicked.connect(_choose_folder_dialog)
        folder_row.addWidget(folder_choose_btn)
        layout.addLayout(folder_row)

        auth_btn = QPushButton("Refresh API Key")
        auth_btn.clicked.connect(lambda: self._refresh_auth_status(force=True))
        layout.addWidget(auth_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)

        dialog.exec()

    def _set_dark_mode(self, enabled: bool):
        self.dark_mode = bool(enabled)
        self._apply_theme(self.dark_mode)
        sa_config.save_config("theme_mode", "dark" if self.dark_mode else "light")

    def closeEvent(self, event):
        sys.stdout = self._orig_stdout
        sys.stderr = self._orig_stderr
        super().closeEvent(event)


def run():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = SermonAudioMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
