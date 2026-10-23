"""`utils/cover_capture.py`：旧曲封面偏糊时借浏览器重拍一张（用户 2026-10-21 反馈）。

2026-10-23 又补了一条：B 站的 `og:image` 带缩放后缀（`…jpg@1200w_630h`），页面上却
根本没有渲染出那张图 —— 所以先问页面要地址、把原图下回来，拍不到才退回截图。
"""
import io
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import TestCase, mock

# ⚠️ 跑测时**绝不能**真的去开浏览器（会卡几十秒，还会抢占用户的桌面）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["VOCAWIKI_NO_BROWSER_FETCH"] = "1"

from PIL import Image                                              # noqa: E402

from models.video import Video, VideoSite                          # noqa: E402
from utils import browser_fetch, cover_capture                     # noqa: E402


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

    def test_full_size_url_drops_the_cdn_resize_suffix(self):
        """B 站 / 别家 CDN 的 `@1200w_630h`、`@96w.webp` 都是缩略变体，取原图要去掉。"""
        self.assertEqual("https://i0.hdslb.com/bfs/archive/abc.jpg",
                         cover_capture.full_size_url(
                             "https://i0.hdslb.com/bfs/archive/abc.jpg@1200w_630h"))
        self.assertEqual("https://i0.hdslb.com/bfs/face/abc.jpg",
                         cover_capture.full_size_url("https://i0.hdslb.com/bfs/face/abc.jpg@96w.webp"))
        self.assertEqual("https://nico.invalid/a.jpg",
                         cover_capture.full_size_url("https://nico.invalid/a.jpg"))


class CaptureCoverTest(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.target = Path(self.folder.name) / "cover_capture.png"
        self.video = Video(VideoSite.NICO_NICO, "sm21899594", "", 0, datetime(2013, 9, 24))

    def _no_declared_url(self):
        return mock.patch.object(browser_fetch, "image_url", return_value=None)

    def test_writes_the_screenshot_as_png(self):
        with self._no_declared_url(), \
             mock.patch.object(browser_fetch, "screenshot", return_value=_png_bytes()) as shot:
            path = cover_capture.capture_cover(self.video, self.target)
        self.assertEqual(self.target, path)
        with Image.open(path) as image:
            self.assertEqual((960, 540), image.size)
        self.assertIn("nicovideo.jp/watch/sm21899594", shot.call_args.args[0])

    def test_declared_cover_url_is_preferred_and_used_full_size(self):
        """B 站：页面声明的 `og:image` 带 `@1200w_630h`，去掉后缀把原图下回来（用户 2026-10-23）。"""
        video = Video(VideoSite.BILIBILI, "BV1U34y1U7Wb", "", 0, datetime(2023, 5, 1))
        response = mock.Mock()
        response.content = _png_bytes((1280, 720))
        with mock.patch.object(browser_fetch, "image_url",
                               return_value="https://i0.hdslb.com/bfs/archive/abc.jpg@1200w_630h") \
                as image_url, \
             mock.patch("utils.cover_capture.http_get", return_value=response) as http_get, \
             mock.patch.object(browser_fetch, "screenshot") as shot:
            path = cover_capture.capture_cover(video, self.target)
        self.assertEqual(self.target, path)
        self.assertIn("bilibili.com/video/BV1U34y1U7Wb", image_url.call_args.args[0])
        self.assertEqual("https://i0.hdslb.com/bfs/archive/abc.jpg",
                         http_get.call_args.args[0])
        shot.assert_not_called()
        with Image.open(path) as image:
            self.assertEqual((1280, 720), image.size)

    def test_no_screenshot_returns_none(self):
        """已删除的稿件没有页面可拍、浏览器也不可用时，调用方保持原图。"""
        with self._no_declared_url(), \
             mock.patch.object(browser_fetch, "screenshot", return_value=None):
            self.assertIsNone(cover_capture.capture_cover(self.video, self.target))
        self.assertFalse(self.target.exists())

    def test_broken_image_bytes_are_ignored(self):
        with self._no_declared_url(), \
             mock.patch.object(browser_fetch, "screenshot", return_value=b"not a png"):
            self.assertIsNone(cover_capture.capture_cover(self.video, self.target))
        self.assertFalse(self.target.exists())

    def test_default_target_lives_in_the_output_folder(self):
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch("config.config.get_output_path", return_value=Path(folder)), \
             self._no_declared_url(), \
             mock.patch.object(browser_fetch, "screenshot", return_value=_png_bytes()):
            path = cover_capture.capture_cover(self.video)
        self.assertEqual(cover_capture.CAPTURE_NAME, Path(path).name)
