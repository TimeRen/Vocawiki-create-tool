"""榜单视频取帧（见 utils/chart_video.py）。"""
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from utils import chart_video as cv


def _jpeg(split=0.5, size=(64, 48)):
    """左边 `split` 是白、右边是黑（不同的 split 得到明显不同的感知哈希）。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, "#000000")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, int(size[0] * split), size[1]], fill="#ffffff")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


class FrameCountTest(unittest.TestCase):
    def test_caps_at_max_frames(self):
        self.assertEqual(400, cv.frame_count(3600, 4.0, 400))

    def test_follows_the_interval(self):
        self.assertEqual(300, cv.frame_count(1200, 4.0, 400))

    def test_unknown_duration_still_asks_for_one_frame(self):
        self.assertEqual(1, cv.frame_count(0, 4.0, 400))
        self.assertEqual(1, cv.frame_count(None, 4.0, 400))

    def test_zero_interval_falls_back_to_four_seconds(self):
        self.assertEqual(50, cv.frame_count(200, 0, 400))


class DedupeTest(unittest.TestCase):
    def test_identical_frames_collapse(self):
        frames = [(0.1, _jpeg(0.5)), (0.2, _jpeg(0.5)), (0.3, _jpeg(0.75))]
        kept = cv.dedupe(frames)
        self.assertEqual([0.1, 0.3], [fraction for fraction, _data in kept])

    def test_different_frames_are_kept(self):
        frames = [(index / 10, _jpeg(split)) for index, split
                  in enumerate((0.125, 0.375, 0.625, 0.875))]
        self.assertEqual(4, len(cv.dedupe(frames)))

    def test_broken_frame_never_counts_as_duplicate(self):
        kept = cv.dedupe([(0.1, b"not an image"), (0.2, b"not an image either")])
        self.assertEqual(2, len(kept))

    def test_mean_hash_is_stable(self):
        data = _jpeg(0.5)
        self.assertEqual(cv.mean_hash(data), cv.mean_hash(data))


class ParseDurationTest(unittest.TestCase):
    def test_ffmpeg_banner(self):
        banner = ("Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'x.mp4':\n"
                  "  Duration: 00:23:34.52, start: 0.000000, bitrate: 200 kb/s")
        self.assertAlmostEqual(1414.52, cv._parse_hhmmss(banner), places=2)

    def test_no_duration(self):
        self.assertEqual(0.0, cv._parse_hhmmss("nothing here"))


class FfmpegTest(unittest.TestCase):
    """真的调一次 ffmpeg：造一个 8 秒的小视频，看能不能抽出帧。"""

    def setUp(self):
        if cv.ffmpeg_path() is None:
            self.skipTest("没有可用的ffmpeg")

    def test_extract_frames_from_a_tiny_video(self):
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / "clip.mp4"
            command = [cv.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
                       "-f", "lavfi", "-i", "testsrc=duration=8:size=160x120:rate=10",
                       "-pix_fmt", "yuv420p", str(clip)]
            try:
                subprocess.run(command, check=True, timeout=120,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except (OSError, subprocess.SubprocessError) as error:
                self.skipTest(f"造测试视频失败：{error}")
            frames = cv.extract_frames(clip, 4)
        self.assertEqual(4, len(frames))
        for fraction, data in frames:
            self.assertGreaterEqual(fraction, 0.0)
            self.assertTrue(data.startswith(b"\xff\xd8"), "应该是JPEG")

    def test_missing_file_is_harmless(self):
        self.assertEqual([], cv.extract_frames(Path("no-such-file.mp4"), 4))


class AvailabilityTest(unittest.TestCase):
    def test_dependency_probe_returns_booleans(self):
        """依赖装没装都能跑（装了就一定是 True，本机 CI 都装了）。"""
        self.assertIsInstance(cv.ytdlp_available(), bool)
        self.assertIsInstance(cv.ffmpeg_path(), (str, type(None)))


if __name__ == "__main__":
    unittest.main()
