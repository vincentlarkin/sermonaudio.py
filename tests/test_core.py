import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sapy import api, cli, config
from sapy.download import Sermon, clean, target_path
from sapy.resolve import resolve


class ResolveTests(unittest.TestCase):
    def check(self, text, kind, value, **extra):
        t = resolve(text)
        self.assertEqual((t.kind, t.value), (kind, value), text)
        for k, v in extra.items():
            self.assertEqual(getattr(t, k), v, text)

    def test_links(self):
        self.check("https://www.sermonaudio.com/sermons/21422247165025", "sermon", "21422247165025")
        self.check("sermonaudio.com/sermons/21422247165025/", "sermon", "21422247165025")
        self.check("https://www.sermonaudio.com/solo/mohler/sermons/102626120117272", "sermon", "102626120117272")
        self.check("https://www.sermonaudio.com/speakers/48786/", "speaker", "48786")
        self.check("https://www.sermonaudio.com/series/36435", "series", "36435")
        self.check("https://www.sermonaudio.com/broadcasters/ghbc/", "broadcaster", "ghbc")
        self.check("https://www.sermonaudio.com/solo/mohler/", "broadcaster", "mohler")
        self.check("https://cloud.sermonaudio.com/media/video/1080p/1234567890.mp4", "sermon", "1234567890",
                   media="video", quality="1080p")
        self.check("https://www.sermonaudio.com/sermoninfo.asp?SID=1234567890", "sermon", "1234567890")
        self.check("https://www.sermonaudio.com/speaker.asp?speaker=Paul+Washer", "speaker", "Paul Washer",
                   by_name=True)

    def test_bare_input(self):
        self.check("21422247165025", "sermon", "21422247165025")
        self.check("paul washer", "search", "paul washer")
        self.assertTrue(resolve("paul washer", "speaker").by_name)
        self.assertFalse(resolve("48786", "speaker").by_name)

    def test_unknown_link(self):
        with self.assertRaises(ValueError):
            resolve("https://example.com/nothing")


class PathTests(unittest.TestCase):
    def test_layout(self):
        s = Sermon("1", title='What is "Faith"?', speaker="Paul Washer", series="Romans: Part 1", date="2019-05-01")
        p = target_path(s, "/root")
        self.assertEqual(p, Path("/root/Paul Washer/Romans Part 1/2019-05-01 - What is Faith.mp3"))
        self.assertEqual(target_path(Sermon("1", title="T", speaker="S"), "/r", "video", "high").name, "T [720p].mp4")

    def test_clean(self):
        self.assertEqual(clean("  a/b\\c  ."), "a b c")
        self.assertEqual(clean(""), "Untitled")

    def test_from_api(self):
        s = Sermon.from_api({"sermonID": 5, "fullTitle": "T", "speaker": {"displayName": "S"},
                             "broadcaster": {"displayName": "B"}, "series": {"title": "X"},
                             "preachDate": "2020-01-02T00:00:00", "hasVideo": False})
        self.assertEqual((s.sermon_id, s.speaker, s.broadcaster, s.series, s.date), ("5", "S", "B", "X", "2020-01-02"))


class SiteKeyTests(unittest.TestCase):
    def test_reads_key_from_nuxt_payload(self):
        payload = [{"state": 1}, {"$sapi:publicKey": 2, "$sauth:user": 3}, "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE", None]
        html = f'<html><script type="application/json" id="__NUXT_DATA__" data-ssr="true">{json.dumps(payload)}</script>'
        resp = mock.Mock(text=html, raise_for_status=lambda: None)
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}), \
                mock.patch.object(api.session, "get", return_value=resp):
            self.assertEqual(api.fetch_site_key(), "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")
            self.assertTrue((config.config_dir() / "site_key.txt").exists())


class ConfigTests(unittest.TestCase):
    def test_set_and_show(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
            self.assertEqual(cli.main(["config", "set", "workers", "5"]), 0)
            self.assertEqual(config.load_config()["workers"], 5)
            self.assertEqual(cli.main(["config", "set", "nope", "1"]), 2)


if __name__ == "__main__":
    unittest.main()
