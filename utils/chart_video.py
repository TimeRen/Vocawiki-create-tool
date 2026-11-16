"""榜单视频取帧：`yt-dlp` 下载最低画质的 mp4，`ffmpeg` 一趟抽出若干帧。

为什么不用浏览器截（`browser_fetch.capture_frames`）：周刊视频有二十几分钟，
浏览器要一帧一帧拖进度条再截图，慢且容易掉帧；下载下来用 ffmpeg 抽帧又快又稳。
但 `yt-dlp` / `ffmpeg` 未必装了（移动端一定没有），所以：

* 依赖**一律懒加载**（`yt_dlp` / `imageio_ffmpeg` 在函数里 import）；
* 拿不到 `yt-dlp` 或 `ffmpeg` 就返回空列表，由调用方退回浏览器截图；
* 两样都没有时整条流程仍然能跑（识别不到就手填，表格里可以直接改）。

抽出来的帧**交给 ffmpeg 的 `mpdecimate` 去重**（用户 2026-11-15 报「没有第 7 名」查出来的）：
以前用 8×8 灰度均值哈希、并且和**所有历史帧**比 —— 周刊卡片版式一模一样，缩到 8×8 之后
差异被平均掉，实测 6 张不同卡片的哈希距离只有 0～1（阈值 6），于是 **6 张被删成 1 张**，
名次就是这么丢的。`mpdecimate` 只跟**上一帧**比、按原始分辨率算差，正好是「同一张卡片
停留好几秒」这个场景：实测同一段 3 张卡片的视频，`fps=1` 出 6 帧、`fps=1,mpdecimate`
出 3 帧（刚好一张卡片一帧）。
"""
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

# 下载用的画质上限：卡片上的字 360p 就能看清，再高只是白等下载时间
MIN_HEIGHT = 360
# 送去识别的帧最长边（模型那边还会再缩一次，这里先压一档省内存）
FRAME_MAX_EDGE = 1280
DOWNLOAD_TIMEOUT = 900


def ytdlp_available() -> bool:
    """装了 `yt-dlp` 没有（没装就退回浏览器截图）。"""
    try:
        import yt_dlp  # noqa: F401
    except Exception:                            # noqa: BLE001 - 移动端/精简安装都没有
        return False
    return True


def ffmpeg_path() -> Optional[str]:
    """`ffmpeg` 可执行文件：先问 `imageio-ffmpeg`（随包分发，用户不用自己装），再找 PATH。"""
    try:
        import imageio_ffmpeg

        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and Path(path).exists():
            return str(path)
    except Exception as e:                       # noqa: BLE001 - 没装就找系统的
        logging.debug("imageio-ffmpeg不可用：%s", e)
    return shutil.which("ffmpeg")


def frame_count(duration: float, interval: float, max_frames: int) -> int:
    """按时长与采样间隔算要抽多少帧（至少 1 帧，最多 `max_frames`）。"""
    if duration is None or duration <= 0:
        return max(1, min(int(max_frames or 1), 1))
    step = interval if interval and interval > 0 else 4.0
    return max(1, min(int(max_frames or 1), int(math.ceil(float(duration) / step))))


def download(video_id: str, folder: Path, max_height: int = MIN_HEIGHT,
             cancel: Optional[Callable[[], None]] = None) -> Tuple[Optional[Path], float]:
    """用 `yt-dlp` 下载稿件（最低画质 mp4）→ `(文件, 时长秒)`；失败返回 `(None, 0)`。

    `cancel` 会在下载过程中被反复调用（`yt-dlp` 的 `progress_hook`）：它抛异常就中断下载，
    异常**原样抛出**（`RunCancelled` 不能被当成「下载失败」吞掉，否则会接着去抽帧）。
    """
    if not ytdlp_available():
        logging.info("没装yt-dlp，榜单视频改为浏览器截图取帧")
        return None, 0.0
    import yt_dlp

    def hook(_status: dict) -> None:
        if cancel is not None:
            cancel()                             # 用户点了「清除对话记录」/切功能 → 立刻中断

    folder.mkdir(parents=True, exist_ok=True)
    url = f"https://www.nicovideo.jp/watch/{video_id}"
    options = {
        # niconico 的画面与声音是分开的两条流，`worst` / `best` 这种「单个文件」的选择器
        # 一条都匹配不上（实测直接 `Requested format is not available`）。
        # 抽帧只要画面，所以取最差的**视频**流（360p 就够看清卡片上的字），不带音轨。
        "format": (f"worstvideo[height<={max_height}][ext=mp4]/worstvideo[ext=mp4]/"
                   "worstvideo/best"),
        "outtmpl": str(folder / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "progress_hooks": [hook],
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
    except Exception as e:                       # noqa: BLE001 - 下载失败就走浏览器
        if _is_cancelled(e):
            raise
        logging.warning("下载榜单视频%s失败：%s", video_id, e)
        return None, 0.0
    if not path.exists():
        candidates = sorted(folder.glob(f"{video_id}.*"))
        path = candidates[0] if candidates else None
    if path is None:
        logging.warning("榜单视频%s下载完却找不到文件", video_id)
        return None, 0.0
    duration = float(info.get("duration") or 0) if isinstance(info, dict) else 0.0
    logging.info("榜单视频%s已下载：%s（%s，%.1f分钟）", video_id, path.name,
                 _size_text(path), duration / 60 if duration else 0)
    return path, duration


def _size_text(path: Path) -> str:
    try:
        return f"{path.stat().st_size / 1048576:.1f}MB"
    except OSError:
        return "大小未知"


def _is_cancelled(error: Exception) -> bool:
    """这个异常是不是「用户放弃了这一轮」（`utils.ui.RunCancelled`）。"""
    name = type(error).__name__
    return name == "RunCancelled"


def extract_frames(path: Path, count: int, max_edge: int = FRAME_MAX_EDGE,
                   duration: float = 0.0) -> List[Tuple[float, bytes]]:
    """从 mp4 里抽帧（JPEG 字节）→ `[(位置 0~1, 字节), …]`。

    一趟 ffmpeg 搞定：`fps` 采样 + **`mpdecimate` 去重**（只跟上一帧比，全分辨率），
    所以「同一张卡片停留好几秒」只会留下一帧，而版式相同、内容不同的卡片不会被吞掉。
    抽不出来返回空列表。
    """
    program = ffmpeg_path()
    if program is None or not path or not Path(path).exists() or count <= 0:
        return []
    step = duration / float(count) if duration and duration > 0 else _frame_step(path, count)
    step = max(0.2, step)
    with tempfile.TemporaryDirectory(prefix="chart_frames_") as folder:
        pattern = str(Path(folder) / "frame_%05d.jpg")
        scale = f"scale='min({max_edge},iw)':-2"
        attempts = (
            f"fps=fps=1/({step}),mpdecimate,{scale}", ["-fps_mode", "vfr"],
            f"fps=fps=1/({step}),mpdecimate,{scale}", ["-vsync", "0"],
            f"fps=fps=1/({step}),{scale}", [],          # 最后退回「不去重」，至少不空手
        )
        done = False
        for index in range(0, len(attempts), 2):
            filters, flags = attempts[index], attempts[index + 1]
            command = [program, "-hide_banner", "-loglevel", "error", "-i", str(path),
                       "-vf", filters, "-frames:v", str(count), "-q:v", "4",
                       "-start_number", "0", *flags, pattern]
            if _run_ffmpeg(command):
                done = True
                break
            logging.debug("抽帧这一组参数不行：%s %s", filters, flags)
        if not done:
            logging.warning("ffmpeg抽帧失败")
            return []
        files = sorted(Path(folder).glob("frame_*.jpg"))
        frames: List[Tuple[float, bytes]] = []
        for index, file in enumerate(files):
            try:
                frames.append((index / max(1, len(files)), file.read_bytes()))
            except OSError as e:
                logging.debug("读帧%s失败：%s", file.name, e)
    logging.info("从榜单视频抽出%d帧（已按mpdecimate去重）", len(frames))
    return frames


def _run_ffmpeg(command: List[str]) -> bool:
    """跑一次 ffmpeg；退出码 0 且没报错返回 True。"""
    try:
        result = subprocess.run(command, timeout=DOWNLOAD_TIMEOUT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except Exception as e:                       # noqa: BLE001 - 抽帧失败就走浏览器
        logging.debug("ffmpeg跑不动：%s", e)
        return False
    if result.returncode == 0:
        return True
    text = (result.stderr or b"").decode("utf-8", "ignore").strip().replace("\n", " ")
    logging.debug("ffmpeg返回% s：%s", result.returncode, text[:160])
    return False


def _frame_step(path: Path, count: int) -> float:
    """ffmpeg 的 `fps` 滤镜要的是「几秒一帧」：查一次时长再除以帧数（查不到按 4 秒）。"""
    program = ffmpeg_path()
    if program is None:
        return 4.0
    try:
        result = subprocess.run([program, "-hide_banner", "-i", str(path)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=120)
    except Exception:                            # noqa: BLE001 - 查不到就按 4 秒算
        return 4.0
    seconds = _parse_hhmmss((result.stderr or b"").decode("utf-8", "ignore"))
    if seconds <= 0:
        return 4.0
    return max(0.2, seconds / max(1, count))


_HHMMSS_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2})\.(\d+)")


def _parse_hhmmss(text: str) -> float:
    match = _HHMMSS_RE.search(text or "")
    if not match:
        return 0.0
    return (int(match.group(1)) * 3600 + int(match.group(2)) * 60
            + int(match.group(3)) + int(match.group(4)) / 100.0)


def mean_hash(data: bytes) -> Optional[int]:
    """8×8 灰度均值哈希（感知哈希）：越像的图，异或后 1 的个数越少。

    ⚠️ **不要用它给周刊卡片去重**（所以这里只有 `mean_hash`，没有 `dedupe`）：
    卡片版式一模一样，缩到 8×8 之后内容差异被平均掉，实测 6 张不同卡片的距离只有 0～1，
    按距离去重会把整期榜单删成 1 张（用户 2026-11-15 报「没有第 7 名」就是这么丢的）。
    去重交给 ffmpeg 的 `mpdecimate`（见 `extract_frames`），这里只留给测试与调试。
    """
    try:
        import io

        from PIL import Image
    except ImportError:
        return None
    try:
        image = Image.open(io.BytesIO(data)).convert("L").resize((8, 8), Image.BILINEAR)
    except Exception as e:                       # noqa: BLE001 - 坏帧就当算不出
        logging.debug("计算感知哈希失败：%s", e)
        return None
    pixels = list(image.tobytes())
    average = sum(pixels) / len(pixels)
    value = 0
    for pixel in pixels:
        value = (value << 1) | (1 if pixel >= average else 0)
    return value


def video_frames(video_id: str, interval: float = 4.0, max_frames: int = 400,
                 workdir: Optional[Path] = None,
                 cancel: Optional[Callable[[], None]] = None) -> List[Tuple[float, bytes]]:
    """下载 + 抽帧一条龙；任何一步不行都返回空列表（调用方退回浏览器截图）。

    * 去重已经交给 ffmpeg 的 `mpdecimate`，这里不再二次去重（免得又把卡片删掉）；
    * `cancel` 会在下载过程中被调用（用户点了「清除对话记录」/切功能就中断）；
    * 没指定 `workdir` 时自己建一个临时目录，用完连视频一起删掉。
    """
    folder = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="chart_video_"))
    own = workdir is None
    try:
        if cancel is not None:
            cancel()
        path, duration = download(video_id, folder, cancel=cancel)
        if path is None:
            return []
        if cancel is not None:
            cancel()
        return extract_frames(path, frame_count(duration, interval, max_frames),
                              duration=duration)
    finally:
        if own:
            cleanup(folder)


def cleanup(folder) -> None:
    """删掉下载下来的视频（抽完帧就没用了，几百 MB 不该留在磁盘上）。"""
    if not folder:
        return
    path = Path(folder)
    if not path.exists():
        return
    for file in path.glob("*"):
        try:
            if file.is_file():
                os.chmod(file, 0o666)
                file.unlink()
        except OSError as e:
            logging.debug("删临时文件%s失败：%s", file, e)
    try:
        path.rmdir()
    except OSError:
        pass


__all__ = ["ytdlp_available", "ffmpeg_path", "frame_count", "download", "extract_frames",
           "mean_hash", "video_frames", "cleanup", "MIN_HEIGHT", "FRAME_MAX_EDGE"]
