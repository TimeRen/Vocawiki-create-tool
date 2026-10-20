"""`utils/cover_capture.py`：旧曲封面偏糊时借浏览器重拍一张（用户 2026-10-21 反馈）。"""
import io
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import TestCase, mock

from PIL import Image

from models.video import Video, VideoSite
from utils import browser_fetch, cover_capture


def _png_bytes(size=(960, 540)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "#39c5bb").save(buffer, "PNG")
    return buffer.getvalue()


class PageUrlTest(TestCase):
    @staticmethod
    def _video(site, identifier="sm1"):
        return Video(site, identifier, "", 0, datetime(2020, 1, 1))

    def test_each_site_has_its_watch_page(self):
        self.assertEqual("https://www.nicovideo.jp/watch/sm21899594",
                         cover_capture.page_url(self._video(VideoSite.NICO_NICO, "sm21899594")))
        self.assertEqual("https://www.bilibili.com/video/BV1U34y1U7Wb",
                         cover_capture.page_url(self._video(VideoSite.BILIBILI, "BV1U34y1U7Wb")))
        self.assertEqual("https://www.youtube.com/watch?v=5moELJhNgro",
                         cover_capture.page_url(self._video(VideoSite.YOUTUBE, "5moELJhNgro")))

    def test_missing_identifier_has_no_page(self):
        self.assertIsNone(cover_capture.page_url(self._video(VideoSite.NICO_NICO, "")))


class CaptureCoverTest(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.target = Path(self.folder.name) / "cover_capture.png"
        self.video = Video(VideoSite.NICO_NICO, "sm21899594", "", 0, datetime(2013, 9, 24))

    def test_writes_the_screenshot_as_png(self):
        with mock.patch.object(browser_fetch, "screenshot", return_value=_png_bytes()) as shot:
            path = cover_capture.capture_cover(self.video, self.target)
        self.assertEqual(self.target, path)
        with Image.open(path) as image:
            self.assertEqual((960, 540), image.size)
        self.assertIn("nicovideo.jp/watch/sm21899594", shot.call_args.args[0])

    def test_no_screenshot_returns_none(self):
        """已删除的稿件没有页面可拍、浏览器也不可用时，调用方保持原图。"""
        with mock.patch.object(browser_fetch, "screenshot", return_value=None):
            self.assertIsNone(cover_capture.capture_cover(self.video, self.target))
        self.assertFalse(self.target.exists())

    def test_broken_image_bytes_are_ignored(self):
        with mock.patch.object(browser_fetch, "screenshot", return_value=b"not a png"):
            self.assertIsNone(cover_capture.capture_cover(self.video, self.target))
        self.assertFalse(self.target.exists())

    def test_default_target_lives_in_the_output_folder(self):
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch("config.config.get_output_path", return_value=Path(folder)), \
             mock.patch.object(browser_fetch, "screenshot", return_value=_png_bytes()):
            path = cover_capture.capture_cover(self.video)
        self.assertEqual(cover_capture.CAPTURE_NAME, Path(path).name)
