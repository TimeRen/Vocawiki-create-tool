import io
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import TestCase, mock

import numpy as np
from PIL import Image

from models.color import Color, get_text_color
from models.video import Video, VideoSite
from utils.image import (MIN_COVER_PIXELS, cover_urls, detect_crop_box, download_first,
                         file_pixels, order_covers, parse_image_size,
                         remove_black_boarders)


class ImageTest(TestCase):
    @staticmethod
    def _video(site: VideoSite, thumb_url, identifier: str = "id") -> Video:
        return Video(site, identifier, "url", 0, datetime(2020, 1, 1), thumb_url=thumb_url)

    @staticmethod
    def _write_image(path: Path, size) -> Path:
        Image.new("RGB", size).save(path)
        return path

    # 条目 column（sm43439171，稿件非公開）的实测 URL
    NICO_URL = "https://nicovideo.cdn.nimg.jp/thumbnails/43439171/43439171.47226299"
    YT_URL = "https://img.youtube.com/vi/abcdefghijk/maxresdefault.jpg"

    def test_parse_image_size(self):
        for fmt in ("PNG", "JPEG", "GIF", "WEBP", "BMP"):
            buf = io.BytesIO()
            Image.new("RGB", (37, 21)).save(buf, format=fmt)
            self.assertEqual((37, 21), parse_image_size(buf.getvalue()), fmt)

    def test_parse_image_size_incomplete(self):
        # 头部数据不足时应返回 None，而不是抛出异常
        self.assertIsNone(parse_image_size(b"\xff\xd8\xff"))
        self.assertIsNone(parse_image_size(b""))

    def test_cover_urls_upgrades_niconico_thumbnail(self):
        # niconico 的小图换成 360x270 的 `.L`（拿不到时会退回原 URL）；其它站点原样返回
        nico = self._video(VideoSite.NICO_NICO, self.NICO_URL)
        self.assertEqual([self.NICO_URL + ".L", self.NICO_URL], cover_urls(nico))
        self.assertEqual([self.YT_URL], cover_urls(self._video(VideoSite.YOUTUBE, self.YT_URL)))
        self.assertEqual([], cover_urls(self._video(VideoSite.BILIBILI, None)))

    def test_order_covers_ignores_blurry_niconico(self):
        # niconico 只有 130x100（非公開视频）时让位给 YouTube 的 maxresdefault
        nico = self._video(VideoSite.NICO_NICO, self.NICO_URL)
        youtube = self._video(VideoSite.YOUTUBE, self.YT_URL)
        with mock.patch("utils.image.remote_image_size", return_value=(130, 100)):
            self.assertEqual([youtube, nico], order_covers([nico, youtube]))

    def test_order_covers_prefers_niconico_on_tie(self):
        # 一样大时仍然让 niconico 优先
        nico = self._video(VideoSite.NICO_NICO, self.NICO_URL)
        youtube = self._video(VideoSite.YOUTUBE, self.YT_URL)
        with mock.patch("utils.image.remote_image_size", return_value=(1280, 720)):
            self.assertEqual([nico, youtube], order_covers([youtube, nico]))

    def test_order_covers_defers_unmeasurable_cover(self):
        # 分辨率认不出的候选（探测失败 / 死链）排最后
        nico = self._video(VideoSite.NICO_NICO, self.NICO_URL)
        youtube = self._video(VideoSite.YOUTUBE, self.YT_URL)
        with mock.patch("utils.image.remote_image_size", return_value=None):
            self.assertEqual([youtube, nico], order_covers([nico, youtube]))

    def test_order_covers_by_resolution(self):
        # 无 niconico 时按识别到的分辨率从大到小排序
        hq = self._video(VideoSite.YOUTUBE, "https://img.youtube.com/vi/abcdefghijk/hqdefault.jpg")
        maxres = self._video(VideoSite.YOUTUBE,
                             "https://img.youtube.com/vi/abcdefghijk/maxresdefault.jpg")
        self.assertEqual([maxres, hq], order_covers([hq, maxres]))
        # 没有缩略图的视频会被忽略
        self.assertEqual([], order_covers([self._video(VideoSite.BILIBILI, None)]))

    def test_file_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_image(Path(tmp).joinpath("a.jpg"), (320, 180))
            self.assertEqual(320 * 180, file_pixels(path))
            self.assertEqual(0, file_pixels(Path(tmp).joinpath("missing.jpg")))
            broken = Path(tmp).joinpath("broken.jpg")
            broken.write_text("不是图片", encoding="utf-8")
            self.assertEqual(0, file_pixels(broken))

    def test_download_first_skips_blurry_cover(self):
        """下载成功但只有 130x100（非公開视频的 niconico 缩略图）时改要 YouTube 的 maxresdefault。"""
        nico = self._video(VideoSite.NICO_NICO, self.NICO_URL)
        youtube = self._video(VideoSite.YOUTUBE, self.YT_URL)
        asked, written = [], []

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            target = tmpdir.joinpath("cover.jpg")

            def fake_download(url, site, index):
                asked.append(url)
                if url.endswith(".L"):                      # 模拟服务器没有 `.L` 大图
                    return None
                size = (130, 100) if site == VideoSite.NICO_NICO else (1280, 720)
                path = self._write_image(tmpdir.joinpath(f"temp{index}.jpeg"), size)
                written.append(path)
                return path

            with mock.patch("utils.image.download_image", side_effect=fake_download):
                image, video = download_first([nico, youtube], target)

            self.assertEqual(youtube, video)                 # 糊的那张被跳过了
            self.assertEqual(1280 * 720, file_pixels(target))
            # niconico 先试 `.L`、再退回原 URL，两张都被放弃；没用上的临时文件不应留下
            self.assertEqual([self.NICO_URL + ".L", self.NICO_URL, self.YT_URL], asked)
            self.assertFalse(any(p.exists() for p in written))
            self.assertEqual([target], list(tmpdir.iterdir()))

    def test_download_first_keeps_blurry_cover_as_last_resort(self):
        """所有来源都只有糊图时仍然给出封面（将就着用，不报错）。"""
        nico = self._video(VideoSite.NICO_NICO, self.NICO_URL)
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            def fake_download(url, site, index):
                if url.endswith(".L"):
                    return None
                return self._write_image(tmpdir.joinpath(f"temp{index}.jpeg"), (130, 100))

            with mock.patch("utils.image.download_image", side_effect=fake_download):
                image, video = download_first([nico], tmpdir.joinpath("cover.jpg"))

            self.assertEqual(nico, video)
            self.assertEqual(130 * 100, file_pixels(image))
            self.assertLess(130 * 100, MIN_COVER_PIXELS)     # 确实低于「够清晰」的下限

    def test_detect_crop_box(self):
        # 上下各 30px 黑边应被自动识别
        arr = np.full((240, 320, 3), 180, np.uint8)
        arr[:30, :, :] = 0
        arr[-30:, :, :] = 0
        self.assertEqual((30, 210, 0, 320), detect_crop_box(Image.fromarray(arr)))

    def test_detect_crop_box_without_border(self):
        # 无黑边时不应裁剪
        arr = np.full((240, 320, 3), 150, np.uint8)
        self.assertEqual((0, 240, 0, 320), detect_crop_box(Image.fromarray(arr)))

    def test_remove_black_boarders(self):
        # 左右各 40px 黑边，裁剪后应为 240x240
        arr = np.full((240, 320, 3), 180, np.uint8)
        arr[:, :40, :] = 0
        arr[:, -40:, :] = 0
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp).joinpath("in.png"), Path(tmp).joinpath("out.png")
            Image.fromarray(arr).save(src)
            remove_black_boarders(src, dst)
            with Image.open(dst) as out:
                self.assertEqual((240, 240), out.size)

    def test_text_color(self):
        black = Color(0, 0, 0)
        white = Color(255, 255, 255)
        self.assertEqual(white, get_text_color(Color(100, 100, 150)))
        self.assertEqual(white, get_text_color(black))
        self.assertEqual(black, get_text_color(white))
        self.assertEqual(black, get_text_color(Color(130, 140, 150)))
        self.assertEqual(black, get_text_color(Color(0, 255, 255)))
        print(Color(4, 156, 161).perceived_lightness())
        print(Color(255, 255, 255).perceived_lightness())
