"""utils/ai_upscale.py 的测试（不联网，waifu2x 请求全部 mock）。"""
import io
import os
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
    def __init__(self, status: int = 200, content: bytes = b"", text: str = ""):
        self.status_code = status
        self.content = content
        self.text = text


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

    def test_reports_http_errors(self):
        session = _Session([_Response(400, text="too large"),
                            _Response(400, text="too large")])
        with mock.patch.object(ai_upscale, "_session", return_value=session):
            result = ai_upscale.upscale_cover(self.cover)

        self.assertFalse(result["ok"])
        self.assertIn("400", result["error"])
        self.assertIn("too large", result["error"])

    def test_missing_cover_is_reported(self):
        result = ai_upscale.upscale_cover(Path(self.folder.name) / "nope.png")
        self.assertFalse(result["ok"])
        self.assertIn("还没有封面", result["error"])

    def test_too_large_cover_is_reported_without_a_request(self):
        session = _Session([])
        with mock.patch.object(ai_upscale, "_session", return_value=session), \
                mock.patch.object(ai_upscale, "MAX_BYTES", 10):
            result = ai_upscale.upscale_cover(self.cover)

        self.assertFalse(result["ok"])
        self.assertIn("6 MB", result["error"])
        self.assertEqual([], session.calls)

    def test_invalid_response_is_reported_and_temp_file_removed(self):
        session = _Session([_Response(content=b"not an image"),
                            _Response(content=b"not an image")])
        with mock.patch.object(ai_upscale, "_session", return_value=session), \
                mock.patch.object(ai_upscale.tempfile, "mkstemp",
                                  return_value=(os.open(os.devnull, os.O_RDONLY),
                                                str(Path(self.folder.name) / "out.png"))):
            result = ai_upscale.upscale_cover(self.cover)

        self.assertFalse(result["ok"])
        self.assertIn("不是有效图片", result["error"])
        self.assertFalse((Path(self.folder.name) / "out.png").exists())
