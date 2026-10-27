import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sapy import api, cli, config, download
from sapy.download import Sermon, _number_series, clean, normalize_quality, quality_chain, target_path
from sapy.resolve import Target, resolve


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

    def test_feed_and_names(self):
        self.check("https://feed.sermonaudio.com/151324", "series", "151324")
        self.assertTrue(resolve("studies in proverbs", "series").by_name)
        self.assertFalse(resolve("151324", "series").by_name)

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
        self.assertEqual(target_path(Sermon("1", title="T", speaker="S"), "/r", "video", "720p").name, "T [720p].mp4")
        self.assertEqual(target_path(Sermon("1", title="T", speaker="S", folder="Church"), "/r").parent.name, "Church")

    def test_clean(self):
        self.assertEqual(clean("  a/b\\c  ."), "a b c")
        self.assertEqual(clean(""), "Untitled")

    def test_from_api(self):
        s = Sermon.from_api({"sermonID": 5, "fullTitle": "T", "speaker": {"displayName": "S"},
                             "broadcaster": {"displayName": "B"}, "series": {"title": "X"},
                             "preachDate": "2020-01-02T00:00:00", "hasVideo": False})
        self.assertEqual((s.sermon_id, s.speaker, s.broadcaster, s.series, s.date), ("5", "S", "B", "X", "2020-01-02"))


class QualityTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_quality("video", "high"), "720p")
        self.assertEqual(normalize_quality("video", "1080"), "1080p")
        self.assertEqual(normalize_quality("video", "nonsense"), "720p")
        self.assertEqual(normalize_quality("audio", "high"), "high")
        self.assertEqual(normalize_quality("audio", "720p"), "high")
        self.assertEqual(normalize_quality("audio", "low"), "low")

    def test_fallback_order(self):
        self.assertEqual(quality_chain("video", "1080p"), ["1080p", "720p", "360p"])
        self.assertEqual(quality_chain("video", "720p"), ["720p", "360p", "1080p"])
        self.assertEqual(quality_chain("video", "360p"), ["360p", "720p", "1080p"])
        self.assertEqual(quality_chain("video", "best"), ["1080p", "720p", "360p"])
        self.assertEqual(quality_chain("audio", "low"), ["low", "high"])
        self.assertEqual(quality_chain("audio", "high"), ["high", "low"])

    def test_cli_choice(self):
        p = cli.build_parser()
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
            choice = lambda args, t=Target("sermon", "1"): cli.media_choice(t, p.parse_args(args))
            self.assertEqual(choice([]), ("audio", "high"))
            self.assertEqual(choice(["--small"]), ("audio", "low"))
            self.assertEqual(choice(["-q", "low"]), ("audio", "low"))
            self.assertEqual(choice(["--video"]), ("video", "720p"))
            self.assertEqual(choice(["--video", "1080p"]), ("video", "1080p"))
            self.assertEqual(choice(["-q", "360p"]), ("video", "360p"))
            self.assertEqual(choice(["--video", "-q", "high"]), ("video", "720p"))
            self.assertEqual(choice([], Target("sermon", "1", media="video", quality="1080p")), ("video", "1080p"))
            config.save_config("video_quality", "best")
            self.assertEqual(choice(["--video"]), ("video", "best"))

    def test_placeholder_is_not_a_download(self):
        resp = mock.MagicMock(status_code=200, headers={"Content-Type": "video/mp4", "Content-Length": "2274516",
                                                        "ETag": '"c0e34c3701be4ef90947afcebefab6d3"'})
        resp.__enter__.return_value = resp
        with tempfile.TemporaryDirectory() as d, mock.patch.object(api.session, "get", return_value=resp):
            self.assertFalse(download._fetch("u", Path(d) / "x.mp4"))
            self.assertIsNone(download.probe("1", "video", "1080p"))
            self.assertFalse((Path(d) / "x.mp4").exists())


class SeriesTests(unittest.TestCase):
    def test_numbering_and_shared_folder(self):
        ss = [Sermon(str(i), speaker=sp, broadcaster="Church") for i, sp in enumerate(["A", "B", "A"])]
        _number_series("Romans", ss)
        self.assertEqual([(s.track, s.track_total, s.folder, s.series) for s in ss],
                         [(1, 3, "Church", "Romans"), (2, 3, "Church", "Romans"), (3, 3, "Church", "Romans")])
        solo = _number_series("John", [Sermon("9", speaker="A")])
        self.assertEqual(solo[0].folder, "")

    def test_group_by_series(self):
        groups = download.group_by_series([Sermon("1", series="X"), Sermon("2"), Sermon("3", series="X"),
                                           Sermon("4", series="Y")])
        self.assertEqual([(t, len(g)) for t, g in groups], [("X", 2), ("Y", 1), ("", 1)])


class SiteKeyTests(unittest.TestCase):
    def test_reads_key_from_nuxt_payload(self):
        payload = [{"state": 1}, {"$sapi:publicKey": 2, "$sauth:user": 3}, "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE", None]
        html = f'<html><script type="application/json" id="__NUXT_DATA__" data-ssr="true">{json.dumps(payload)}</script>'
        resp = mock.Mock(text=html, raise_for_status=lambda: None)
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}), \
                mock.patch.object(api.session, "get", return_value=resp):
            self.assertEqual(api.fetch_site_key(), "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")
            self.assertTrue((config.config_dir() / "site_key.txt").exists())

    def test_falls_back_to_js_bundles(self):
        page = mock.Mock(text='<html><link href="/_nuxt/abc.js">', raise_for_status=lambda: None)
        bundle = mock.Mock(text='x={publicKey:"11111111-2222-3333-4444-555555555555"}')
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}), \
                mock.patch.object(api.session, "get", side_effect=[page, bundle]):
            self.assertEqual(api.fetch_site_key(), "11111111-2222-3333-4444-555555555555")


class ConfigTests(unittest.TestCase):
    def test_set_and_show(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
            self.assertEqual(cli.main(["config", "set", "workers", "5"]), 0)
            self.assertEqual(config.load_config()["workers"], 5)
            self.assertEqual(cli.main(["config", "set", "nope", "1"]), 2)


if __name__ == "__main__":
    unittest.main()
