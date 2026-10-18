"""utils/ai_upscale.py 的测试（不联网，waifu2x 请求全部 mock）。"""
import io
import random
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from PIL import Image

from utils import ai_upscale


def _png(size=(2, 2), color="#39c5bb") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class _Response:
    def __init__(self, status: int = 200, content: bytes = b"", text: str = "",
                 content_type: str = "image/png"):
        self.status_code = status
        self.content = content
        self.text = text
        self.headers = {"content-type": content_type}


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class UpscaleCoverTest(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.cover = Path(self.folder.name) / "cover.png"
        self.cover.write_bytes(_png((2, 3)))

    def test_upscales_and_returns_a_temporary_png(self):
        session = _Session([_Response(content=_png((4, 6)))])
        with mock.patch.object(ai_upscale, "_session", return_value=session):
            result = ai_upscale.upscale_cover(self.cover)

        self.assertTrue(result["ok"], result)
        self.assertEqual((4, 6), (result["width"], result["height"]))
        self.assertEqual((2, 3), (result["sourceWidth"], result["sourceHeight"]))
        output = Path(result["path"])
        self.addCleanup(output.unlink, True)
        self.assertTrue(output.is_file())
        with Image.open(output) as image:
            self.assertEqual((4, 6), image.size)
        url, kwargs = session.calls[0]
        self.assertEqual(ai_upscale.API_URLS[0], url)
        self.assertEqual({"scale": "2", "noise": "1", "style": "art"}, kwargs["data"])
        self.assertIn("cover.png", kwargs["files"]["file"][0])

    def test_falls_back_to_the_second_endpoint(self):
        session = _Session([OSError("连不上"), _Response(content=_png((4, 6)))])
        with mock.patch.object(ai_upscale, "_session", return_value=session):
            result = ai_upscale.upscale_cover(self.cover)

        self.assertTrue(result["ok"], result)
        self.addCleanup(Path(result["path"]).unlink, True)
        self.assertEqual([ai_upscale.API_URLS[0], ai_upscale.API_URLS[1]],
                         [call[0] for call in session.calls])

    def test_http_errors_fall_back_to_local_upscaling(self):
        session = _Session([_Response(400, text="too large", content_type="text/plain"),
                            _Response(400, text="too large", content_type="text/plain")])
        with mock.patch.object(ai_upscale, "_session", return_value=session):
            result = ai_upscale.upscale_cover(self.cover)

        self.assertTrue(result["ok"], result)
        self.assertEqual("local", result["engine"])
        self.assertIn("本地超分", result["message"])
        self.assertIn("400", result["warning"])
        self.addCleanup(Path(result["path"]).unlink, True)
        with Image.open(result["path"]) as image:
            self.assertEqual((4, 6), image.size)

    def test_missing_cover_is_reported(self):
        result = ai_upscale.upscale_cover(Path(self.folder.name) / "nope.png")
        self.assertFalse(result["ok"])
        self.assertIn("还没有封面", result["error"])

    def test_oversized_upload_is_shrunk_to_the_api_limit(self):
        cover = Path(self.folder.name) / "big.png"
        rng = random.Random(0)
        pixels = bytes(rng.randrange(256) for _ in range(200 * 200 * 3))
        Image.frombytes("RGB", (200, 200), pixels).save(cover)
        session = _Session([_Response(content=_png((4, 6)))])
        with mock.patch.object(ai_upscale, "_session", return_value=session), \
                mock.patch.object(ai_upscale, "MAX_BYTES", 5000):
            result = ai_upscale.upscale_cover(cover)

        self.assertTrue(result["ok"], result)
        self.addCleanup(Path(result["path"]).unlink, True)
        uploaded = session.calls[0][1]["files"]["file"]
        self.assertLessEqual(len(uploaded[1]), 5000, "上传前要压到接口上限以内")
        self.assertTrue(uploaded[0].endswith((".png", ".jpg")))

    def test_oversized_pixels_are_shrunk_before_upload(self):
        """waifu2x 的输入像素上限是 1500×1500（2 倍放大后不超过 3000×3000）。"""
        cover = Path(self.folder.name) / "huge.png"
        Image.new("RGB", (200, 200), "#39c5bb").save(cover)
        session = _Session([_Response(content=_png((4, 6)))])
        with mock.patch.object(ai_upscale, "_session", return_value=session), \
                mock.patch.object(ai_upscale, "MAX_PIXELS", 100 * 100):
            result = ai_upscale.upscale_cover(cover)

        self.assertTrue(result["ok"], result)
        self.addCleanup(Path(result["path"]).unlink, True)
        uploaded = session.calls[0][1]["files"]["file"][1]
        with Image.open(io.BytesIO(uploaded)) as sent:
            self.assertLessEqual(sent.width * sent.height, 100 * 100)

    def test_invalid_response_falls_back_to_local_upscaling(self):
        session = _Session([_Response(content=b"<html>blocked</html>",
                                      content_type="text/html"),
                            _Response(content=b"<html>blocked</html>",
                                      content_type="text/html")])
        with mock.patch.object(ai_upscale, "_session", return_value=session):
            result = ai_upscale.upscale_cover(self.cover)

        self.assertTrue(result["ok"], result)
        self.assertEqual("local", result["engine"])
        self.assertIn("不是图片", result["warning"])
        self.addCleanup(Path(result["path"]).unlink, True)
        with Image.open(result["path"]) as image:
            self.assertEqual((4, 6), image.size)

    def test_webp_or_gif_like_input_is_normalized_to_png(self):
        source = Path(self.folder.name) / "cover.bmp"
        Image.new("RGB", (2, 3), "#39c5bb").save(source, format="BMP")
        session = _Session([_Response(content=_png((4, 6)))])
        with mock.patch.object(ai_upscale, "_session", return_value=session):
            result = ai_upscale.upscale_cover(source)

        self.assertTrue(result["ok"], result)
        self.addCleanup(Path(result["path"]).unlink, True)
        filename, data, mime = session.calls[0][1]["files"]["file"]
        self.assertTrue(filename.endswith(".png"), filename)
        self.assertEqual("image/png", mime)
        self.assertTrue(data.startswith(b"\x89PNG"), "上传前统一转成 PNG")
