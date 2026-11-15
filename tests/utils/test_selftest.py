"""打包后的依赖自检（见 utils/selftest.py）。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils import selftest


class RequestedTest(unittest.TestCase):
    def test_flag_detection(self):
        self.assertTrue(selftest.requested(["exe", "--selftest-deps", "r.json"]))
        self.assertFalse(selftest.requested(["exe", "--console"]))
        self.assertFalse(selftest.requested([]))


class ReportPathTest(unittest.TestCase):
    def test_uses_the_next_argument(self):
        self.assertEqual(Path("out.json"),
                         selftest.report_path(["exe", "--selftest-deps", "out.json"]))

    def test_falls_back_when_the_path_is_missing(self):
        self.assertEqual(Path("selftest-deps.json"),
                         selftest.report_path(["exe", "--selftest-deps"]))
        self.assertEqual(Path("selftest-deps.json"),
                         selftest.report_path(["exe", "--selftest-deps", "--console"]))


class CollectTest(unittest.TestCase):
    def test_report_shape(self):
        report = selftest.collect()
        self.assertEqual(report["ok"], not report["problem"])
        self.assertIsInstance(report["problem"], list)
        self.assertIn("python", report)
        self.assertIsInstance(report["ytdlp_available"], bool)
        # 装上的依赖要报出版本号，没装的要进 problem
        for name in ("yt_dlp", "imageio_ffmpeg"):
            if name in report["packages"]:
                self.assertTrue(report["packages"][name])
            else:
                self.assertTrue(any(name.replace("_", "-") in item
                                    for item in report["problem"]), name)

    def test_missing_ffmpeg_is_reported(self):
        with mock.patch("utils.chart_video.ffmpeg_path", return_value=None):
            report = selftest.collect()
        self.assertFalse(report["ok"])
        self.assertTrue(any("ffmpeg" in item for item in report["problem"]))

    def test_run_writes_the_report(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sub" / "deps.json"
            code = selftest.run(["exe", "--selftest-deps", str(path)])
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(0 if data["ok"] else 1, code)


if __name__ == "__main__":
    unittest.main()
