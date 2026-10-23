"""`vocadb.process_image()`：下载 / 截图回来的封面要变成**真正的** JPEG。

背景（用户 2026-10-23）：封面可能是浏览器截出来的 PNG（`cover_capture.capture_cover()`
存的就是 PNG），也可能来自只回 PNG 的 CDN；而提交用的文件名一直是 `<条目名>.jpg`。
上传时 MIME 是按**文件名**判的，内容却是 PNG —— 站点会认不上。
"""
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

from PIL import Image

from utils import vocadb
from utils.image import is_jpeg


def _config(crop: bool):
    return SimpleNamespace(image=SimpleNamespace(crop=crop))


class ProcessImageTest(TestCase):
    def test_png_content_becomes_a_real_jpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp).joinpath("cover.jpg")       # 名字是 .jpg、内容是 PNG
            Image.new("RGB", (96, 54), "#39c5bb").save(source, "PNG")
            target = Path(tmp).joinpath("Redial封面.jpg")
            with mock.patch.object(vocadb, "get_config", return_value=_config(crop=False)):
                vocadb.process_image(source, target)
            self.assertTrue(is_jpeg(target), "内容必须真的是 JPEG")
            with Image.open(target) as saved:
                self.assertEqual((96, 54), saved.size)
            self.assertFalse(source.exists(), "临时的那份挪走了")

    def test_a_jpeg_is_just_moved(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp).joinpath("temp0.jpeg")
            Image.new("RGB", (40, 30), "#123456").save(source, "JPEG", quality=90)
            raw = source.read_bytes()
            target = Path(tmp).joinpath("Redial封面.jpg")
            with mock.patch.object(vocadb, "get_config", return_value=_config(crop=False)):
                vocadb.process_image(source, target)
            self.assertEqual(raw, target.read_bytes(), "已经是 JPEG：原样搬过去，不重新编码")

    def test_cropping_still_writes_the_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp).joinpath("temp0.jpeg")
            Image.new("RGB", (64, 36), "#39c5bb").save(source, "JPEG")
            target = Path(tmp).joinpath("Redial封面.jpg")
            with mock.patch.object(vocadb, "get_config", return_value=_config(crop=True)):
                vocadb.process_image(source, target)
            self.assertTrue(is_jpeg(target))

    def test_missing_source_does_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).joinpath("Redial封面.jpg")
            with mock.patch.object(vocadb, "get_config", return_value=_config(crop=False)):
                vocadb.process_image(Path(tmp).joinpath("nope.jpeg"), target)
            self.assertFalse(target.exists())
