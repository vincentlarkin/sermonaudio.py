import importlib
import io
import pathlib
import unittest


class SaGuiQtTests(unittest.TestCase):
    def test_module_import_and_entrypoints(self):
        mod = importlib.import_module("sapy.gui")
        self.assertTrue(hasattr(mod, "SermonAudioMainWindow"))
        self.assertTrue(callable(mod.run))

    def test_text_helpers(self):
        mod = importlib.import_module("sapy.gui")
        self.assertEqual(mod._safe_text(None, "x"), "x")
        self.assertEqual(mod._safe_text("  abc  "), "abc")
        self.assertEqual(mod._sermon_title({"fullTitle": "Hello"}), "Hello")
        self.assertEqual(mod._sermon_title({"displayTitle": "World"}), "World")
        self.assertEqual(mod._speaker_name({}), "Unknown speaker")
        self.assertEqual(mod._broadcaster_name({}), "Unknown broadcaster")

    def test_log_stream_write_passthrough(self):
        mod = importlib.import_module("sapy.gui")
        sink = io.StringIO()
        stream = mod.GuiLogStream(sink)
        count = stream.write("abc")
        self.assertEqual(count, 3)
        self.assertEqual(sink.getvalue(), "abc")

    def test_source_uses_qt_tabs_not_flet(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        source = (root / "sapy" / "gui.py").read_text(encoding="utf-8")
        self.assertIn("QTabWidget", source)
        self.assertIn("self.tabs.addTab(self._build_search_tab(), \"Search\")", source)
        self.assertIn("self.tabs.addTab(self._build_manual_tab(), \"Manual Download\")", source)
        self.assertIn("self.tabs.addTab(self._build_queue_tab(), \"Queue\")", source)
        self.assertIn("class CollapsibleSection", source)
        self.assertIn("self.search_scroll = QScrollArea()", source)
        self.assertIn("self.speakers_section = CollapsibleSection(\"Speakers\", start_collapsed=True)", source)
        self.assertIn("self.sermons_section = CollapsibleSection(\"Sermons\", start_collapsed=True)", source)
        self.assertIn("self.series_section = CollapsibleSection(\"Serieses\", start_collapsed=True)", source)
        self.assertNotIn("self.search_tabs = QTabWidget()", source)
        self.assertIn("QFileDialog.getExistingDirectory", source)
        self.assertNotIn("import flet", source.lower())
        self.assertIn("SearchViewport", source)
        self.assertIn("LogViewport", source)
        self.assertIn("QLineEdit::placeholder", source)
        self.assertIn("QCheckBox::indicator:checked", source)
        self.assertIn("PanelLabel", source)
        self.assertIn("CollapseToggle", source)
        self.assertIn("ResultTitle", source)
        self.assertIn("ResultMeta", source)


if __name__ == "__main__":
    unittest.main()
