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


class MeanHashTest(unittest.TestCase):
    def test_mean_hash_is_stable(self):
        data = _jpeg(0.5)
        self.assertEqual(cv.mean_hash(data), cv.mean_hash(data))

    def test_broken_frame_returns_none(self):
        self.assertIsNone(cv.mean_hash(b"not an image"))

    def test_same_layout_cards_hash_almost_the_same(self):
        """这就是**为什么不能用它去重**：版式相同的卡片（只有小字不同）距离只有 0～1，
        按距离去重会把整期榜单删成一张（用户 2026-11-15 报「没有第 7 名」）。"""
        from PIL import Image, ImageDraw

        hashes = []
        for title in ("Lilium", "カンタレラ", "炉心融解"):
            image = Image.new("RGB", (480, 360), "#f4f0e8")
            draw = ImageDraw.Draw(image)
            draw.rectangle([30, 60, 210, 250], fill="#dddddd")
            draw.text((60, 12), title, fill="#cc3333")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG")
            hashes.append(cv.mean_hash(buffer.getvalue()))
        distances = [bin(left ^ right).count("1")
                     for left, right in zip(hashes, hashes[1:])]
        self.assertLessEqual(max(distances), 2, distances)


class ProgressTest(unittest.TestCase):
    """底部「正在…（N%）」用的两个进度源。"""

    def test_download_fraction_from_bytes(self):
        self.assertEqual(0.5, cv._download_fraction(
            {"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100}))
        self.assertEqual(0.25, cv._download_fraction(
            {"downloaded_bytes": 25, "total_bytes_estimate": 100}))
        self.assertEqual(1.0, cv._download_fraction({"status": "finished"}))
        self.assertIsNone(cv._download_fraction({"downloaded_bytes": 50}))   # 不知道总量
        self.assertIsNone(cv._download_fraction({}))
        self.assertIsNone(cv._download_fraction({"downloaded_bytes": None,
                                                 "total_bytes": 0}))


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

    def test_progress_is_reported_while_extracting(self):
        """`-progress pipe:1` 的 `out_time_us` → 0~1（底部「正在抽帧…（48%）」）。"""
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
            seen = []
            cv.extract_frames(clip, 4, duration=8.0, progress=seen.append)
        self.assertTrue(seen, "抽帧没有报过进度")
        self.assertEqual(1.0, seen[-1])
        self.assertEqual(seen, sorted(seen), "进度不能往回跳")

    def test_same_layout_cards_are_not_merged(self):
        """三张**版式相同、只有小字不同**的卡片放在一段视频里 → 必须抽出 3 帧。

        这是用户 2026-11-15 报「没有第 7 名」的回归测试：以前用 8×8 均值哈希跟所有历史帧
        比，这种卡片距离 0～2，三张会被删成一张。
        """
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for index, title in enumerate(("Lilium", "カンタレラ", "炉心融解")):
                image = Image.new("RGB", (480, 360), "#f4f0e8")
                draw = ImageDraw.Draw(image)
                draw.text((20, 12), str(index + 1), fill="#cc3333")
                draw.text((60, 12), title, fill="#cc3333")
                draw.rectangle([30, 60, 210, 250], fill="#dddddd")
                image.save(root / f"card{index}.jpg")
                (root / f"card{index}.txt").write_text(
                    f"file '{(root / f'card{index}.jpg').as_posix()}'\nduration 2.5\n",
                    encoding="utf-8")
            (root / "list.txt").write_text(
                "".join((root / f"card{index}.txt").read_text(encoding="utf-8")
                        for index in range(3)), encoding="utf-8")
            clip = root / "clip.mp4"
            try:
                subprocess.run([cv.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
                                "-f", "concat", "-safe", "0", "-i", str(root / "list.txt"),
                                "-vf", "fps=25,format=yuv420p", "-c:v", "libx264",
                                str(clip)], check=True, timeout=120,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except (OSError, subprocess.SubprocessError) as error:
                self.skipTest(f"造测试视频失败：{error}")
            frames = cv.extract_frames(clip, 3, duration=7.5)
        self.assertEqual(3, len(frames), "版式相同的不同卡片被当成重复删掉了")


class AvailabilityTest(unittest.TestCase):
    def test_dependency_probe_returns_booleans(self):
        """依赖装没装都能跑（装了就一定是 True，本机 CI 都装了）。"""
        self.assertIsInstance(cv.ytdlp_available(), bool)
        self.assertIsInstance(cv.ffmpeg_path(), (str, type(None)))


if __name__ == "__main__":
    unittest.main()
