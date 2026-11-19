"""榜单视频取帧（见 utils/chart_video.py）。"""
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class FrameBudgetTest(unittest.TestCase):
    """`-frames:v` 用的是「采样点数 + 余量」，不是 `max_frames`（用户 2026-11-18 报的丢片尾）。"""

    def test_budget_covers_a_thirty_minute_video(self):
        """30 分钟按 2 秒采样 → 911 个点，预算必须够（旧写法 400 会在 63% 处断掉）。"""
        self.assertGreaterEqual(cv.frame_budget(400, 1822.0, 2.0), 943)

    def test_budget_stays_under_the_hard_cap(self):
        """异常长的视频别把内存吃光：最多 `max_frames * 3`。"""
        self.assertEqual(1200, cv.frame_budget(400, 100000.0, 2.0))

    def test_small_window_uses_its_own_sample_count(self):
        """重采窗口：`count=24` 时上限是 72 张，60 秒的窗口按 1 秒采样也够（60+32 > 72）。"""
        self.assertEqual(72, cv.frame_budget(24, 60.0, 1.0))

    def test_never_returns_zero(self):
        self.assertGreaterEqual(cv.frame_budget(0, 0.0, 2.0), 1)


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

    def test_extract_frames_from_a_window(self):
        """带 `-ss` 的窗口抽帧要真的能出图（mjpeg 对「非全范围 YUV」会直接失败）。"""
        if cv.ffmpeg_path() is None:
            self.skipTest("没有可用的ffmpeg")
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
            frames = cv.extract_frames(clip, 10, duration=8.0, start=4.0, end=8.0, step=0.5)
        self.assertTrue(frames, "窗口抽帧一帧都没出来")
        for fraction, data in frames:
            self.assertGreaterEqual(fraction, 0.4, "位置要是整片里的真实时间")
            self.assertTrue(data.startswith(b"\xff\xd8"))

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


class ShowinfoTimeTest(unittest.TestCase):
    """帧位置要用 `showinfo` 的真实时间（以前写的是「去重后序号 / 帧数」）。"""

    STDERR = """
[Parsed_showinfo_2 @ 000001] n:   0 pts:      0 pts_time:0.5    pos:  1 fmt:yuv420p
[Parsed_showinfo_2 @ 000001] n:   1 pts:   1000 pts_time:12.25 pos:  2 fmt:yuv420p
"""

    def test_parses_pts_time_in_order(self):
        self.assertEqual([0.5, 12.25], cv.parse_showinfo_times(self.STDERR))

    def test_no_showinfo_gives_no_times(self):
        self.assertEqual([], cv.parse_showinfo_times("no such thing"))

    def test_position_uses_real_time(self):
        self.assertAlmostEqual(50.0 / 100.0, cv._position([50.0], 0, 4, 100.0, 0.0))

    def test_position_falls_back_to_index_without_pts(self):
        self.assertAlmostEqual(0.25, cv._position([], 1, 4, 100.0, 0.0))

    def test_position_adds_the_window_start(self):
        # 重采是从第 30 秒开始的：pts 是相对窗口的，得加回去
        self.assertAlmostEqual(31.0 / 100.0, cv._position([1.0], 0, 4, 100.0, 30.0))


class DenseSamplingTest(unittest.TestCase):
    """配置里的 4 秒采样会漏卡片，`prepare_frames` 必须封顶到 `DENSE_STEP`。"""

    def test_step_is_capped(self):
        seen = {}

        def fake_extract(path, count, max_edge=0, duration=0.0, progress=None,
                         start=0.0, end=0.0, step=0.0):
            seen["count"] = count
            seen["step"] = step
            return [(0.0, b"\xff\xd8")]

        with mock.patch.object(cv, "download", return_value=(Path("x.mp4"), 1200.0)), \
             mock.patch.object(cv, "extract_frames", side_effect=fake_extract):
            frames, path, duration = cv.prepare_frames("sm1", 4.0, 400)
        self.assertEqual(1, len(frames))
        self.assertEqual(1200.0, duration)
        self.assertEqual(cv.DENSE_STEP, seen["step"])
        self.assertEqual(400, seen["count"], "上限仍然是配置里的 max_frames")

    def test_short_interval_is_kept(self):
        seen = {}

        def fake_extract(path, count, max_edge=0, duration=0.0, progress=None,
                         start=0.0, end=0.0, step=0.0):
            seen["step"] = step
            return []

        with mock.patch.object(cv, "download", return_value=(Path("x.mp4"), 600.0)), \
             mock.patch.object(cv, "extract_frames", side_effect=fake_extract):
            cv.prepare_frames("sm1", 1.0, 400)
        self.assertEqual(1.0, seen["step"])


class ResampleTest(unittest.TestCase):
    def test_window_is_passed_as_seconds(self):
        seen = {}

        def fake_extract(path, count, max_edge=0, duration=0.0, progress=None,
                         start=0.0, end=0.0, step=0.0):
            seen.update(count=count, start=start, end=end, step=step)
            return []

        with mock.patch.object(cv, "extract_frames", side_effect=fake_extract):
            cv.resample_frames(Path("x.mp4"), 30.0, 55.0, duration=1200.0)
        self.assertEqual(30.0, seen["start"])
        self.assertEqual(55.0, seen["end"])
        self.assertLessEqual(seen["step"], cv.RESAMPLE_STEP)

    def test_empty_window_is_skipped(self):
        self.assertEqual([], cv.resample_frames(Path("x.mp4"), 10.0, 10.0))


class StreamingTest(unittest.TestCase):
    """带进度跑 ffmpeg 时**必须同时把 stderr 读掉**（用户 2026-11-18 报的「抽帧卡住」）。

    真凶：`-loglevel info` 每帧一行 `showinfo` 写到 stderr，Windows 匿名管道只有 4 KB，
    写满就阻塞 ffmpeg，而旧代码要等 stdout 读完才去读 stderr → 双方互等，永远不返回。
    这里用一个「往 stderr 灌 200 KB」的假进程复现：修好之前这个测试会挂死。
    """

    def _fake(self, folder: Path, body: str) -> Path:
        script = folder / "fake_ffmpeg.py"
        script.write_text(body, encoding="utf-8")
        return script

    def test_stderr_flood_does_not_deadlock(self):
        import sys
        import time

        with tempfile.TemporaryDirectory() as folder:
            script = self._fake(Path(folder), (
                "import sys\n"
                "for n in range(4000):\n"
                "    sys.stderr.write('[Parsed_showinfo @ x] n: %d pts_time:%.2f\\n' % (n, n / 10))\n"
                "    sys.stdout.write('out_time_us=%d\\n' % (n * 1000))\n"
                "sys.stdout.write('progress=end\\n')\n"
            ))
            seen = []
            start = time.time()
            ok, output = cv._run_ffmpeg_streaming([sys.executable, str(script)],
                                                  seen.append, duration=400.0)
            elapsed = time.time() - start
        self.assertTrue(ok, "假进程正常退出，应该报成功")
        self.assertLess(elapsed, 60.0, "stderr 灌满管道时不能卡住")
        self.assertIn(b"pts_time", output, "showinfo 的内容要能带回来解析帧位置")
        self.assertIn(1.0, seen, "读到 progress=end 要报 100%")

    def test_timeout_kills_the_child(self):
        import sys

        with tempfile.TemporaryDirectory() as folder:
            script = self._fake(Path(folder), "import time\ntime.sleep(600)\n")
            with mock.patch.object(cv, "DOWNLOAD_TIMEOUT", 1):
                ok, _output = cv._run_ffmpeg_streaming([sys.executable, str(script)],
                                                       lambda _f: None, duration=10.0)
        self.assertFalse(ok, "超时必须报失败，不能一直等")


class VideoCacheTest(unittest.TestCase):
    """下载过的视频要能复用（用户 2026-11-18：同一期重跑别再从 niconico 下 65MB）。"""

    def test_cached_video_skips_ytdlp(self):
        from utils import chart_video as cv_local

        with tempfile.TemporaryDirectory() as folder:
            cached = cv_local.cache_path_for(folder, "sm123")
            cached.write_bytes(b"x" * 1024)
            seen = []
            with mock.patch.object(cv_local, "_probe_duration", return_value=1200.0), \
                 mock.patch.object(cv_local, "ytdlp_available", return_value=True), \
                 mock.patch.dict("sys.modules", {"yt_dlp": mock.Mock()}):
                path, duration = cv_local.download("sm123", Path(folder), progress=(
                    lambda fraction, detail="": seen.append((fraction, detail))))
        self.assertEqual(cached, path)
        self.assertEqual(1200.0, duration)
        self.assertEqual([(1.0, "沿用已下载的视频")], seen)

    def test_missing_cache_still_downloads(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(cv, "ytdlp_available", return_value=False):
                path, duration = cv.download("sm404", Path(folder))
        self.assertIsNone(path)
        self.assertEqual(0.0, duration)

    def test_prune_keeps_the_newest_and_counts_removals(self):
        import os

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for index in range(4):
                path = root / f"sm{index}.mp4"
                path.write_bytes(b"x")
                os.utime(path, (1000 + index, 1000 + index))
            removed = cv.prune_videos(root, keep=2)
            left = sorted(p.name for p in root.glob("*.mp4"))
        self.assertEqual(2, removed)
        self.assertEqual(["sm2.mp4", "sm3.mp4"], left, "只留最新的两个")

    def test_prune_zero_clears_everything(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sm1.mp4").write_bytes(b"x")
            (root / "sm2.mp4.part").write_bytes(b"x")
            cv.prune_videos(root, keep=0)
            left = sorted(p.name for p in root.iterdir())
        self.assertEqual([], left)


class AvailabilityTest(unittest.TestCase):
    def test_dependency_probe_returns_booleans(self):
        """依赖装没装都能跑（装了就一定是 True，本机 CI 都装了）。"""
        self.assertIsInstance(cv.ytdlp_available(), bool)
        self.assertIsInstance(cv.ffmpeg_path(), (str, type(None)))
        self.assertIsInstance(cv.hls_decrypter_available(), bool)


class DownloadTuningTest(unittest.TestCase):
    """`chart.download_parallel` / `download_chunk_size` → yt-dlp 的下载选项。

    用户 2026-11-19 拿 songloft#305 来问「下载慢是不是同一个原因」：niconico 的 HLS 是
    AES-128 加密的，yt-dlp 缺 `pycryptodomex` 就把整条流甩给 ffmpeg 一条连接顺序拉
    （实测 30KB/s）。修法 = 装 pycryptodomex + 打开分片并发，所以这两项必须真的传进 yt-dlp。
    """

    def _fake_chart(self, **values):
        chart = mock.Mock(spec=["download_parallel", "download_chunk_size"])
        for key, value in values.items():
            setattr(chart, key, value)
        return chart

    def _download_options(self, chart) -> dict:
        """跑一次 `download()`（yt-dlp 换成假的），返回传给 yt-dlp 的 options。"""
        seen = {}

        class FakeYDL:
            def __init__(self, options):
                seen.update(options)

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def extract_info(self, _url, download=True):
                (folder / "sm1.mp4").write_bytes(b"x")
                return {"id": "sm1", "ext": "mp4", "duration": 60.0}

            def prepare_filename(self, info):
                return str(folder / f"{info['id']}.mp4")

        fake_config = mock.Mock()
        fake_config.chart = chart
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            with mock.patch.object(cv, "ytdlp_available", return_value=True), \
                 mock.patch.object(cv, "_probe_duration", return_value=60.0), \
                 mock.patch.dict("sys.modules", {"yt_dlp": mock.Mock(YoutubeDL=FakeYDL)}), \
                 mock.patch("config.config.get_config", return_value=fake_config):
                cv.download("sm1", folder)
        return seen

    def test_defaults_are_parallel_and_no_chunking(self):
        self.assertEqual((cv.FRAGMENT_PARALLEL, cv.HTTP_CHUNK_SIZE), cv.download_tuning())
        self.assertGreater(cv.FRAGMENT_PARALLEL, 1, "默认必须并发，串行就是被限速的 30KB/s")
        self.assertEqual(0, cv.HTTP_CHUNK_SIZE, "HLS 小分片再分块反而更慢，默认不开")

    def test_config_drives_the_ytdlp_options(self):
        options = self._download_options(
            self._fake_chart(download_parallel=8, download_chunk_size=4 * 1024 * 1024))
        self.assertEqual(8, options.get("concurrent_fragment_downloads"))
        self.assertEqual(4 * 1024 * 1024, options.get("http_chunk_size"))

    def test_zero_chunk_size_is_not_passed(self):
        """分块关掉时干脆不传这个键（yt-dlp 见到 0 会当成「每个分片再切一刀」）。"""
        options = self._download_options(
            self._fake_chart(download_parallel=4, download_chunk_size=0))
        self.assertEqual(4, options.get("concurrent_fragment_downloads"))
        self.assertNotIn("http_chunk_size", options)

    def test_missing_config_falls_back_to_constants(self):
        with mock.patch("config.config.get_config", side_effect=RuntimeError("没有配置文件")):
            self.assertEqual((cv.FRAGMENT_PARALLEL, cv.HTTP_CHUNK_SIZE), cv.download_tuning())


if __name__ == "__main__":
    unittest.main()
