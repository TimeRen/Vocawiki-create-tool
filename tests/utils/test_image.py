import io
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import TestCase, mock

import numpy as np
from PIL import Image

from models.color import Color, get_text_color
from models.video import Video, VideoSite
from utils.image import (MIN_COVER_PIXELS, HEAVY_BORDER_RATIO, cover_urls, detect_border_box,
                         detect_crop_box, download_first, download_thumbnail, file_pixels,
                         order_covers, parse_image_size, remove_black_boarders, save_as_cover,
                         usable_pixels)

image_download_thumbnail = download_thumbnail      # 名字短一点，测试里读起来更顺


class ImageTest(TestCase):
    @staticmethod
    def _size_of(path: Path):
        with Image.open(path) as opened:
            return opened.size

    @staticmethod
    def _video(site: VideoSite, thumb_url, identifier: str = "id") -> Video:
        return Video(site, identifier, "url", 0, datetime(2020, 1, 1), thumb_url=thumb_url)

    @staticmethod
    def _write_image(path: Path, size) -> Path:
        Image.new("RGB", size).save(path)
        return path

    @staticmethod
    def _write_letterboxed(path: Path, size=(1280, 720), top=150, bottom=87, level=100) -> Path:
        """写一张「宽画幅 + 黑边」的封面（实测《暮光剧场》nico 缩略图就是这个形状）。"""
        arr = np.full((size[1], size[0], 3), level, np.uint8)
        arr[:top, :, :] = 0
        arr[size[1] - bottom:, :, :] = 0
        Image.fromarray(arr).save(path)
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

    def test_cover_urls_upgrades_a_thumbnail_without_the_size_suffix(self):
        """nicolog 给的地址**没有** `.数字` 那一段（实测 sm21899594）。

        以前正则只认 `…/123.456` 这种，这类地址被漏掉 → 用的就是 130×100 的最小图
        （用户 2026-10-21 反馈「这图好糊啊」）。现在同样换成 360×270 的 `.L`。
        """
        url = "https://nicovideo.cdn.nimg.jp/thumbnails/21899594/21899594"
        self.assertEqual([url + ".L", url], cover_urls(self._video(VideoSite.NICO_NICO, url)))

    def test_download_thumbnail_upgrades_a_blurry_cover_with_a_browser_capture(self):
        """偏糊的封面（360×270）用浏览器重拍一张更大的替换掉（用户 2026-10-21 要求）。"""
        nico = self._video(VideoSite.NICO_NICO, self.NICO_URL)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).joinpath("cover.jpg")
            small = self._write_image(Path(tmp).joinpath("temp0.jpeg"), (360, 270))
            captured = self._write_image(Path(tmp).joinpath("cover_capture.png"), (960, 540))
            with mock.patch("utils.image.get_output_path", return_value=Path(tmp)), \
                 mock.patch("utils.image.download_cover_file", return_value=small), \
                 mock.patch("utils.cover_capture.capture_cover",
                            return_value=captured) as capture:
                chosen = image_download_thumbnail([nico], "cover.jpg")
            self.assertIsNotNone(chosen)
            self.assertEqual((960, 540), self._size_of(target))
            self.assertFalse(captured.exists(), "截下来的临时图要挪成封面")
            capture.assert_called_once()

    def test_download_thumbnail_keeps_the_downloaded_cover_when_the_capture_is_smaller(self):
        nico = self._video(VideoSite.NICO_NICO, self.NICO_URL)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).joinpath("cover.jpg")
            small = self._write_image(Path(tmp).joinpath("temp0.jpeg"), (360, 270))
            captured = self._write_image(Path(tmp).joinpath("cover_capture.png"), (200, 112))
            with mock.patch("utils.image.get_output_path", return_value=Path(tmp)), \
                 mock.patch("utils.image.download_cover_file", return_value=small), \
                 mock.patch("utils.cover_capture.capture_cover", return_value=captured):
                image_download_thumbnail([nico], "cover.jpg")
            self.assertEqual((360, 270), self._size_of(target), "拍小了就保持原图")

    def test_download_thumbnail_survives_a_failed_capture(self):
        """截图失败 / 浏览器不可用：封面照旧，生成流程不受影响。"""
        nico = self._video(VideoSite.NICO_NICO, self.NICO_URL)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).joinpath("cover.jpg")
            small = self._write_image(Path(tmp).joinpath("temp0.jpeg"), (360, 270))
            with mock.patch("utils.image.get_output_path", return_value=Path(tmp)), \
                 mock.patch("utils.image.download_cover_file", return_value=small), \
                 mock.patch("utils.cover_capture.capture_cover", return_value=None):
                chosen = image_download_thumbnail([nico], "cover.jpg")
            self.assertIsNotNone(chosen)
            self.assertEqual((360, 270), self._size_of(target))

    def test_download_thumbnail_does_not_capture_a_clean_cover(self):
        """本来就够清楚的封面不该去开浏览器（省时间、也别多一次网络请求）。"""
        nico = self._video(VideoSite.NICO_NICO, self.NICO_URL)
        with tempfile.TemporaryDirectory() as tmp:
            big = self._write_image(Path(tmp).joinpath("temp0.jpeg"), (960, 540))
            with mock.patch("utils.image.get_output_path", return_value=Path(tmp)), \
                 mock.patch("utils.image.download_cover_file", return_value=big), \
                 mock.patch("utils.cover_capture.capture_cover") as capture:
                image_download_thumbnail([nico], "cover.jpg")
            capture.assert_not_called()

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

    def test_detect_crop_box_keeps_the_wide_design(self):
        """裁完比 2:1 还宽时不裁（用户 2026-09-29 报封面变成 1137x482 的长条）。

        实测《暮光剧场》的 nico 封面：1280x720 的画布，内容是 1137x482（2.36:1）的
        超宽封面美术，上下垫了纯黑边 —— 黑边属于整幅设计的一部分，裁掉只会更难看的。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_letterboxed(Path(tmp).joinpath("cover.png"))
            with Image.open(path) as img:
                # 原始黑边探测还是照常（黑边存在、可测）
                self.assertEqual((150, 633, 0, 1280), detect_border_box(img))
                # 但真要裁时会发现只剩 2.65:1 → 保留原图
                self.assertEqual((0, 720, 0, 1280), detect_crop_box(img))

    def test_remove_black_boarders_keeps_the_wide_design(self):
        # 同上：尺寸一个字都不能变
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp).joinpath("in.png"), Path(tmp).joinpath("out.png")
            self._write_letterboxed(src)
            remove_black_boarders(src, dst)
            with Image.open(dst) as out:
                self.assertEqual((1280, 720), out.size)

    def test_usable_pixels_counts_the_black_borders(self):
        with tempfile.TemporaryDirectory() as tmp:
            clean = self._write_image(Path(tmp).joinpath("clean.png"), (1280, 720))
            self.assertEqual((1280 * 720, 0.0), usable_pixels(clean))
            bordered = self._write_letterboxed(Path(tmp).joinpath("border.png"))
            usable, border = usable_pixels(bordered)
            self.assertEqual(1280 * 483, usable)
            self.assertGreater(border, HEAVY_BORDER_RATIO)

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

    def test_black_border_fallback_without_numpy(self):
        """Android 不打包 numpy：黑边检测和裁剪要用纯 PIL 走通。"""
        arr = np.full((240, 320, 3), 180, np.uint8)
        arr[:, :40, :] = 0
        arr[:, -40:, :] = 0
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp).joinpath("in.png"), Path(tmp).joinpath("out.png")
            Image.fromarray(arr).save(src)
            with mock.patch("utils.image.np", None):
                with Image.open(src) as img:
                    self.assertEqual((0, 240, 40, 280), detect_border_box(img))
                remove_black_boarders(src, dst)
            with Image.open(dst) as out:
                self.assertEqual((240, 240), out.size)

    def test_download_first_prefers_the_source_without_black_bars(self):
        """同样清晰时不要「自带黑边、裁完只剩长条」的那张，改要干净的（用户 2026-09-29 报）。

        实测《暮光剧场》：niconico 与 YouTube 的缩略图都是 1280x720，旧规则打平让 nico 优先，
        而 nico 那张自带黑边 → 裁完 1137x482；YouTube 那张是干净的实拍画面。
        """
        nico = self._video(VideoSite.NICO_NICO, self.NICO_URL)
        youtube = self._video(VideoSite.YOUTUBE, self.YT_URL)
        asked = []

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            target = tmpdir.joinpath("cover.jpg")

            def fake_download(url, site, index):
                asked.append(url)
                if url.endswith(".L"):                      # 模拟服务器没有 `.L` 大图
                    return None
                path = tmpdir.joinpath(f"temp{index}.jpeg")
                if site == VideoSite.NICO_NICO:
                    return self._write_letterboxed(path)     # 自带黑边的那张
                return self._write_image(path, (1280, 720))  # 干净的

            with mock.patch("utils.image.download_image", side_effect=fake_download):
                image, video = download_first([nico, youtube], target)

            self.assertEqual(youtube, video)
            self.assertEqual(1280 * 720, file_pixels(target))
            self.assertEqual(0.0, usable_pixels(target)[1])      # 拿到的是干净的那张
            self.assertEqual([self.NICO_URL + ".L", self.NICO_URL, self.YT_URL], asked)
            self.assertEqual([target], list(tmpdir.iterdir()))   # 带黑边的不留垃圾

    def test_download_first_keeps_the_bordered_one_when_nothing_is_clean(self):
        """所有来源都自带黑边时，取可用画面最多的那张（实测《你嘲笑我那天》就是这样）。"""
        nico = self._video(VideoSite.NICO_NICO, self.NICO_URL)
        youtube = self._video(VideoSite.YOUTUBE, self.YT_URL)

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            def fake_download(url, site, index):
                if url.endswith(".L"):
                    return None
                path = tmpdir.joinpath(f"temp{index}.jpeg")
                # nico 的可用画面更小（内容更扁）→ 应该选 YouTube 那张
                top = 150 if site == VideoSite.NICO_NICO else 30
                return self._write_letterboxed(path, top=top, bottom=top)

            with mock.patch("utils.image.download_image", side_effect=fake_download):
                image, video = download_first([nico, youtube], tmpdir.joinpath("cover.jpg"))

            self.assertEqual(youtube, video)
            self.assertEqual(1280 * 660, usable_pixels(image)[0])

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


class SaveAsCoverTest(TestCase):
    """`save_as_cover`：用户在样式页「导入图片」选的那张要写成提交用的封面。"""

    def test_converts_to_jpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp).joinpath("导入.png")
            Image.new("RGBA", (80, 60), (57, 197, 187, 128)).save(source)
            target = Path(tmp).joinpath("out", "Redial封面.jpg")
            self.assertTrue(save_as_cover(source, target))
            with Image.open(target) as saved:
                self.assertEqual("JPEG", saved.format)
                self.assertEqual((80, 60), saved.size)
                self.assertEqual("RGB", saved.mode)
            self.assertTrue(target.parent.is_dir(), "输出目录不在时自己建")

    def test_keeps_the_size_and_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).joinpath("封面.jpg")
            Image.new("RGB", (10, 10)).save(target)
            source = Path(tmp).joinpath("new.png")
            Image.new("RGB", (333, 222)).save(source)
            self.assertTrue(save_as_cover(source, target))
            with Image.open(target) as saved:
                self.assertEqual((333, 222), saved.size, "不缩放、不裁剪，导入的就是提交的")

    def test_unreadable_file_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp).joinpath("not-an-image.jpg")
            source.write_text("这不是图片", encoding="utf-8")
            target = Path(tmp).joinpath("封面.jpg")
            self.assertFalse(save_as_cover(source, target))
            self.assertFalse(target.exists(), "写不成就别留半成品")
