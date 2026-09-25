import io
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import TestCase

import numpy as np
from PIL import Image

from models.color import Color, get_text_color
from models.video import Video, VideoSite
from utils.image import (detect_crop_box, order_covers, parse_image_size,
                         remove_black_boarders)


class ImageTest(TestCase):
    @staticmethod
    def _video(site: VideoSite, thumb_url, identifier: str = "id") -> Video:
        return Video(site, identifier, "url", 0, datetime(2020, 1, 1), thumb_url=thumb_url)

    def test_parse_image_size(self):
        for fmt in ("PNG", "JPEG", "GIF", "WEBP", "BMP"):
            buf = io.BytesIO()
            Image.new("RGB", (37, 21)).save(buf, format=fmt)
            self.assertEqual((37, 21), parse_image_size(buf.getvalue()), fmt)

    def test_parse_image_size_incomplete(self):
        # 头部数据不足时应返回 None，而不是抛出异常
        self.assertIsNone(parse_image_size(b"\xff\xd8\xff"))
        self.assertIsNone(parse_image_size(b""))

    def test_order_covers_prefers_niconico(self):
        # niconico 的 OGP 封面优先级最高，即使其他站点分辨率更大
        nico = self._video(VideoSite.NICO_NICO, "https://example.com/nico.jpg")
        youtube = self._video(VideoSite.YOUTUBE,
                              "https://img.youtube.com/vi/abcdefghijk/maxresdefault.jpg")
        self.assertEqual([nico, youtube], order_covers([youtube, nico]))

    def test_order_covers_by_resolution(self):
        # 无 niconico 时按识别到的分辨率从大到小排序
        hq = self._video(VideoSite.YOUTUBE, "https://img.youtube.com/vi/abcdefghijk/hqdefault.jpg")
        maxres = self._video(VideoSite.YOUTUBE,
                             "https://img.youtube.com/vi/abcdefghijk/maxresdefault.jpg")
        self.assertEqual([maxres, hq], order_covers([hq, maxres]))
        # 没有缩略图的视频会被忽略
        self.assertEqual([], order_covers([self._video(VideoSite.BILIBILI, None)]))

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
