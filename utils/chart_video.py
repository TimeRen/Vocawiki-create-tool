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
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

# 下载用的画质上限：卡片上的字 360p 就能看清，再高只是白等下载时间
MIN_HEIGHT = 360
# 送去识别的帧最长边（模型那边还会再缩一次，这里先压一档省内存）
FRAME_MAX_EDGE = 1280
DOWNLOAD_TIMEOUT = 900
# 采样步长上限（秒）。用户 2026-11-17 报「主榜 1～5 名没读到」查出来：以前按 4 秒一个
# 采样点，24.8 分钟只采 372 个点，**停留不到 4 秒的卡片根本没被采到**（TOPIC 段 11 张也丢了 2 张）。
# 去重交给 `mpdecimate`，所以采样密一点只是多解码、输出帧数不会暴涨。
DENSE_STEP = 2.0
# 「时间轴缺口重采」用的步长：卡片一般停留 1 秒以上，0.2 秒足够不漏
RESAMPLE_STEP = 0.2
# 缺口重采单段的输出帧上限（几十秒的窗口最多也就几张卡）
RESAMPLE_MAX_FRAMES = 24


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


def cache_path_for(folder, video_id: str) -> Path:
    """缓存里这个稿件应该叫什么（`<目录>/<sm号>.mp4`；稳定文件名 → yt-dlp 能断点续传）。"""
    return Path(folder) / f"{video_id}.mp4"


def download(video_id: str, folder: Path, max_height: int = MIN_HEIGHT,
             cancel: Optional[Callable[[], None]] = None,
             progress: Optional[Callable[[float, str], None]] = None
             ) -> Tuple[Optional[Path], float]:
    """用 `yt-dlp` 下载稿件（最低画质 mp4）→ `(文件, 时长秒)`；失败返回 `(None, 0)`。

    `cancel` 会在下载过程中被反复调用（`yt-dlp` 的 `progress_hook`）：它抛异常就中断下载，
    异常**原样抛出**（`RunCancelled` 不能被当成「下载失败」吞掉，否则会接着去抽帧）。
    `progress(0~1, 附加文字)` 让界面底部能显示「正在下载…183KB/s，剩4分05秒（32%）」。

    **已经下过的文件直接复用**（用户 2026-11-18：同一期重跑时白等 6 分钟下载 65MB）：
    调用方把 `folder` 指到缓存目录，这里看到 `<sm号>.mp4` 已经存在就跳过 yt-dlp，
    连 `getthumbinfo` 的往返都省了（时长由 `prepare_frames` 从文件里读）。
    """
    folder = Path(folder)
    cached = cache_path_for(folder, video_id)
    if cached.exists() and cached.stat().st_size > 0:
        logging.info("榜单视频%s已在缓存里（%s），跳过下载", video_id, _size_text(cached))
        if progress is not None:
            progress(1.0, "沿用已下载的视频")
        return cached, _probe_duration(cached)
    if not ytdlp_available():
        logging.info("没装yt-dlp，榜单视频改为浏览器截图取帧")
        return None, 0.0
    import yt_dlp

    started = time.time()

    def hook(status: dict) -> None:
        if cancel is not None:
            cancel()                             # 用户点了「清除对话记录」/切功能 → 立刻中断
        if progress is None:
            return
        fraction = _download_fraction(status or {})
        if fraction is not None:
            progress(fraction, _speed_text(status or {}, started))

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
        # 断了就接着下（配合稳定文件名，重跑不用从头来）
        "continuedl": True,
        "retries": 3,
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
    elapsed = max(0.001, time.time() - started)
    logging.info("榜单视频%s已下载：%s（%s，%.1f分钟，用时%.0f秒 = %.0fKB/s）", video_id,
                 path.name, _size_text(path), duration / 60 if duration else 0,
                 elapsed, _size_of(path) / elapsed / 1024)
    if progress is not None:
        progress(1.0, f"已下载{_size_text(path)}")
    return path, duration


def _size_of(path: Path) -> float:
    try:
        return float(path.stat().st_size)
    except OSError:
        return 0.0


def _speed_text(status: dict, started: float) -> str:
    """进度字典 → 「183KB/s，剩4分05秒」（算不出来就返回空串，不硬编）。"""
    done = float(status.get("downloaded_bytes") or 0)
    total = float(status.get("total_bytes") or status.get("total_bytes_estimate") or 0)
    elapsed = max(0.001, time.time() - started)
    if done <= 0:
        return ""
    speed = done / elapsed
    if speed <= 0:
        return ""
    parts = [f"{speed / 1024:.0f}KB/s"]
    if total > done:
        parts.append("剩" + _duration_text((total - done) / speed))
    return "，".join(parts)


def _duration_text(seconds: float) -> str:
    """秒 → 「4分05秒」/「12秒」（给状态行用，别出现 245.3 秒这种）。"""
    seconds = max(0, int(round(float(seconds or 0))))
    if seconds < 60:
        return f"{seconds}秒"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}分{rest:02d}秒"


def _probe_duration(path: Path) -> float:
    """问 ffmpeg 要时长（缓存命中时用；问不到就返回 0，后面按帧数兜底）。"""
    program = ffmpeg_path()
    if program is None:
        return 0.0
    try:
        result = subprocess.run([program, "-hide_banner", "-i", str(path)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except Exception:                            # noqa: BLE001 - 查不到就按 0 处理
        return 0.0
    return _parse_hhmmss((result.stderr or b"").decode("utf-8", "ignore"))


def prune_videos(folder, keep: int = 3) -> int:
    """只留最近 `keep` 个缓存视频（按修改时间）→ 删掉几个。

    `keep <= 0` 表示**不缓存**：把整个目录清掉（用户选「每次重新下载」时的语义）。
    """
    folder = Path(folder)
    if not folder.exists():
        return 0
    videos = sorted((p for p in folder.glob("*.mp4") if p.is_file()),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    limit = max(0, int(keep or 0))
    removed = 0
    for path in videos[limit:]:
        try:
            path.unlink()
            removed += 1
        except OSError as error:
            logging.debug("删旧缓存视频%s失败：%s", path.name, error)
    if limit == 0:
        for part in folder.glob("*.part"):
            try:
                part.unlink()
            except OSError:
                pass
    if removed:
        logging.info("缓存视频清理：删掉%d个旧的（保留最近%d个）", removed, limit)
    return removed


def _size_text(path: Path) -> str:
    try:
        return f"{path.stat().st_size / 1048576:.1f}MB"
    except OSError:
        return "大小未知"


def _download_fraction(status: dict) -> Optional[float]:
    """yt-dlp 的进度字典 → 0~1（拿不到总大小就返回 None，不硬编一个数）。"""
    if str(status.get("status") or "") == "finished":
        return 1.0
    done = status.get("downloaded_bytes")
    total = status.get("total_bytes") or status.get("total_bytes_estimate")
    try:
        done_value, total_value = float(done or 0), float(total or 0)
    except (TypeError, ValueError):
        return None
    if total_value <= 0:
        return None
    return max(0.0, min(1.0, done_value / total_value))


def _is_cancelled(error: Exception) -> bool:
    """这个异常是不是「用户放弃了这一轮」（`utils.ui.RunCancelled`）。"""
    name = type(error).__name__
    return name == "RunCancelled"


def extract_frames(path: Path, count: int, max_edge: int = FRAME_MAX_EDGE,
                   duration: float = 0.0,
                   progress: Optional[Callable[[float], None]] = None,
                   start: float = 0.0, end: float = 0.0, step: float = 0.0
                   ) -> List[Tuple[float, bytes]]:
    """从 mp4 里抽帧（JPEG 字节）→ `[(位置 0~1, 字节), …]`。

    一趟 ffmpeg 搞定：`fps` 采样 + **`mpdecimate` 去重**（只跟上一帧比，全分辨率），
    所以「同一张卡片停留好几秒」只会留下一帧，而版式相同、内容不同的卡片不会被吞掉。
    抽不出来返回空列表。

    * `step`：采样步长（秒）；不给就按 `时长 / count` 算（旧行为）；
    * `start` / `end`：只抽这一段（秒，`end=0` 表示到片尾）——「时间轴缺口重采」用；
    * 位置一律是**真实时间 / 总时长**（靠 `showinfo` 的 `pts_time`，解析不到才退回
      「序号 / 帧数」，那时位置只是相对顺序，不是时间）。

    `-frames:v` 用的是**本次的采样预算**而不是 `count`（用户 2026-11-18 的怀疑查出来的）：
    `count`（配置里的 `max_frames`，默认 400）本来只该是「别抽太多」的硬上限，但写成
    `-frames:v 400` 是「输出满 400 张就停」—— 30 分钟的视频按 2 秒采样有 911 个采样点，
    去重后仍有 627 张，于是**第 1152 秒（全片 63%）额度就用完，最后 11 分钟（主榜 1→4 名、
    TOPIC 段、ED）根本没扫到**。现在按「整段有多少个采样点」给预算，再夹到
    `count * 3` 这个保险上限里。

    `progress(0~1)` 用 ffmpeg 的 `-progress pipe:1` 报「已经处理到全片的第几秒」
    （除以时长），界面底部就能显示「正在抽帧…（48%）」。
    """
    program = ffmpeg_path()
    if program is None or not path or not Path(path).exists() or count <= 0:
        return []
    span = max(0.0, float(end) - float(start)) if end and end > start else 0.0
    if step and step > 0:
        step = max(0.05, float(step))
    else:
        basis = span or (duration if duration and duration > 0 else 0.0)
        step = (basis / float(count)) if basis > 0 else _frame_step(path, count)
        step = max(0.2, step)
    total = duration if duration and duration > 0 else _frame_step(path, count) * count
    budget = frame_budget(count, span or total, step)
    with tempfile.TemporaryDirectory(prefix="chart_frames_") as folder:
        pattern = str(Path(folder) / "frame_%05d.jpg")
        scale = f"scale='min({max_edge},iw)':-2"
        base = f"fps=fps=1/({step}),mpdecimate,showinfo,{scale}"
        attempts = (
            base, ["-fps_mode", "vfr"],
            base, ["-vsync", "0"],
            # 最后退回「不去重」，至少不空手
            f"fps=fps=1/({step}),showinfo,{scale}", [],
        )
        seek = ["-ss", f"{float(start):.3f}", "-t", f"{span:.3f}"] if span else []
        done = False
        output: bytes = b""
        for index in range(0, len(attempts), 2):
            filters, flags = attempts[index], attempts[index + 1]
            # showinfo 写在 info 级别，所以这里不能再用 `-loglevel error`
            command = [program, "-hide_banner", "-loglevel", "info", *seek,
                       "-i", str(path), "-vf", filters, "-frames:v", str(budget),
                       "-q:v", "4", # `-ss` 输入定位后 mjpeg 编码器会抱怨「非全范围 YUV」并失败
                       "-pix_fmt", "yuvj420p", "-start_number", "0", *flags, pattern]
            ok, output = _run_ffmpeg(command, progress=progress, duration=total,
                                     capture=True)
            if ok:
                done = True
                break
            logging.debug("抽帧这一组参数不行：%s %s", filters, flags)
        if not done:
            logging.warning("ffmpeg抽帧失败")
            return []
        files = sorted(Path(folder).glob("frame_*.jpg"))
        times = parse_showinfo_times((output or b"").decode("utf-8", "ignore"))
        frames: List[Tuple[float, bytes]] = []
        for index, file in enumerate(files):
            try:
                data = file.read_bytes()
            except OSError as e:
                logging.debug("读帧%s失败：%s", file.name, e)
                continue
            frames.append((_position(times, index, len(files), total, start), data))
    covered = (max(times) if times else 0.0) + float(start)
    if start or end:
        logging.info("从榜单视频%.0f～%.0f秒抽出%d帧（重采，覆盖到%.0f秒）", start,
                     start + span or start, len(frames), covered)
    else:
        logging.info("从榜单视频抽出%d帧（已按mpdecimate去重，覆盖到%.0f/%.0f秒）",
                     len(frames), covered, total)
        # 只有「预算真的用完了」才警告：短视频本来就不该有几帧
        if total > 0 and len(frames) >= budget and covered < total * 0.95:
            logging.warning("抽帧只覆盖到第%.0f秒（全片%.0f秒）：采样预算%d张用完，"
                            "片尾的名次可能读不到", covered, total, budget)
    return frames


def frame_budget(count: int, span: float, step: float) -> int:
    """这次最多让 ffmpeg 输出多少张：按采样点数给，再夹到 `count * 3` 的保险上限。

    `count` 是配置里的 `max_frames`（用户改它就是想「少抽一点」），所以真正的上限是
    `count * 3`；而「够不够扫完全片」由 `span / step` 决定。两者取小的那个，
    再加 32 张余量（`mpdecimate` 偶尔会多留几张）。这样既不会像以前那样在 63% 处断掉，
    也不会因为配置写小了就白扫。
    """
    samples = int(math.ceil(max(0.0, float(span or 0)) / max(0.05, float(step or 0.05))))
    cap = max(1, int(count or 1)) * 3
    return max(1, min(cap, samples + 32))


_SHOWINFO_TIME_RE = re.compile(r"pts_time:\s*([0-9]+(?:\.[0-9]+)?)")


def parse_showinfo_times(text: str) -> List[float]:
    """ffmpeg `showinfo` 的 stderr → 每张输出帧的 `pts_time`（按帧顺序）。"""
    return [float(match.group(1)) for match in _SHOWINFO_TIME_RE.finditer(str(text or ""))]


def _position(times: Sequence[float], index: int, total: int, duration: float,
              start: float) -> float:
    """第 `index` 帧在整片里的位置（0~1）：有 pts 就用真实时间，否则退回「序号 / 帧数」。"""
    fallback = index / max(1, total)
    if index < len(times) and duration and duration > 0:
        return max(0.0, min(1.0, (float(start) + times[index]) / float(duration)))
    return fallback


def resample_frames(path: Path, start_seconds: float, end_seconds: float,
                    duration: float = 0.0, step: float = RESAMPLE_STEP,
                    max_frames: int = RESAMPLE_MAX_FRAMES,
                    max_edge: int = FRAME_MAX_EDGE) -> List[Tuple[float, bytes]]:
    """把 `[start_seconds, end_seconds]` 这一段按 `step` 重采一遍（时间轴缺口用）。

    卡片按名次顺序播，所以「名次缺失 = 时间轴上的空档」：把空档按 0.2 秒重采，
    `mpdecimate` 去重后通常只剩几张卡，读一遍就能补回来。
    """
    begin = max(0.0, float(start_seconds or 0.0))
    finish = float(end_seconds or 0.0)
    if finish <= begin:
        return []
    span = finish - begin
    budget = int(max_frames or RESAMPLE_MAX_FRAMES)
    return extract_frames(path, budget, max_edge=max_edge, duration=duration,
                          start=begin, end=finish,
                          step=min(float(step or RESAMPLE_STEP), max(0.05, span / budget)))


def _run_ffmpeg(command: List[str], progress: Optional[Callable[[float], None]] = None,
                duration: float = 0.0, capture: bool = False):
    """跑一次 ffmpeg → 成功返回 True（`capture=True` 时返回 `(是否成功, stderr 字节)`）。

    传了 `progress` 就加上 `-progress pipe:1 -nostats`，从 stdout 读 `out_time_us=`
    换算成「处理到全片的第几成」，让底部能显示百分比。
    `capture=True` 把 stderr 一起带回去（`showinfo` 的 `pts_time` 就在里面）。
    """
    if progress is not None:
        command = command[:1] + ["-progress", "pipe:1", "-nostats"] + command[1:]
    try:
        if progress is None:
            result = subprocess.run(command, timeout=DOWNLOAD_TIMEOUT,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            ok = result.returncode == 0
            output = result.stderr or b""
        else:
            ok, output = _run_ffmpeg_streaming(command, progress, duration)
    except Exception as e:                       # noqa: BLE001 - 抽帧失败就走浏览器
        logging.debug("ffmpeg跑不动：%s", e)
        return (False, b"") if capture else False
    if ok:
        return (True, output or b"") if capture else True
    text = (output or b"").decode("utf-8", "ignore").strip().replace("\n", " ")
    logging.debug("ffmpeg返回失败：%s", text[:160])
    return (False, output or b"") if capture else False


def _run_ffmpeg_streaming(command: List[str], progress: Callable[[float], None],
                          duration: float) -> Tuple[bool, bytes]:
    """带进度地跑 ffmpeg → `(是否成功, stderr 字节)`。

    **stderr 必须併进 stdout 一起读**（用户 2026-11-18 报「抽帧卡住不动」查出来的）：
    `-loglevel info` 会把 `showinfo` 每帧一行写到 stderr，Windows 的匿名管道缓冲只有 4 KB，
    写满之后 ffmpeg 就卡在 write() 上不再产出 stdout，而这里正在等 stdout —— 双方互等，
    永远不返回（实测 30 分钟的视频卡在 0.7%、只写出 5 张图，ffmpeg 进程 CPU 停在 2.2 秒）。
    合成一条流之后就不再有第二个没人读的管道。

    同时补上总超时：读循环本身也会无限等（子进程一句话都不说就永远阻塞在 readline 上），
    所以用**看门狗线程**到点直接 kill，进程死了管道自然 EOF，循环立刻结束。
    """
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    chunks: List[bytes] = []
    killed = threading.Event()

    def watchdog() -> None:
        try:
            process.kill()
        except Exception:                        # noqa: BLE001 - 已经退出了
            pass
        killed.set()

    timer = threading.Timer(float(DOWNLOAD_TIMEOUT), watchdog)
    timer.daemon = True
    timer.start()
    try:
        for raw in process.stdout or []:
            chunks.append(raw)
            line = raw.decode("utf-8", "ignore").strip()
            if line.startswith("out_time_us=") and duration > 0:
                try:
                    seconds = float(line.split("=", 1)[1] or 0) / 1_000_000.0
                except ValueError:
                    continue
                progress(max(0.0, min(1.0, seconds / duration)))
            elif line == "progress=end":
                progress(1.0)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            watchdog()
    finally:
        timer.cancel()
        try:
            process.stdout.close()
        except Exception:                        # noqa: BLE001
            pass
    output = b"".join(chunks)
    if killed.is_set():
        logging.warning("ffmpeg抽帧超过%d秒，已中断", int(DOWNLOAD_TIMEOUT))
        return False, output or b"timeout"
    return process.returncode == 0, output


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
                 cancel: Optional[Callable[[], None]] = None,
                 progress: Optional[Callable[[str, float, str], None]] = None
                 ) -> List[Tuple[float, bytes]]:
    """下载 + 抽帧一条龙；任何一步不行都返回空列表（调用方退回浏览器截图）。

    * 去重已经交给 ffmpeg 的 `mpdecimate`，这里不再二次去重（免得又把卡片删掉）；
    * `cancel` 会在下载过程中被调用（用户点了「清除对话记录」/切功能就中断）；
    * `progress(阶段, 0~1)`：阶段是 `"download"` / `"extract"`，给底部显示百分比用；
    * 没指定 `workdir` 时自己建一个临时目录，用完连视频一起删掉。

    采样步长取 `min(interval, DENSE_STEP)`：`interval` 是配置项（默认 4 秒），
    实践下来 4 秒会漏掉停留短的卡片，所以这里封顶 2 秒（去重后帧数不会暴涨）。
    """
    frames, _path, _duration = prepare_frames(video_id, interval, max_frames, workdir,
                                              cancel, progress)
    return frames


def prepare_frames(video_id: str, interval: float = 4.0, max_frames: int = 400,
                   workdir: Optional[Path] = None,
                   cancel: Optional[Callable[[], None]] = None,
                   progress: Optional[Callable[[str, float, str], None]] = None
                   ) -> Tuple[List[Tuple[float, bytes]], Optional[Path], float]:
    """同 `video_frames`，另外把「下载下来的 mp4 路径 / 时长」一起返回。

    时间轴缺口重采要用同一个 mp4 再抽几段，所以有了这个版本：调用方自己给 `workdir`
    就不会被删掉，跑完自己调 `cleanup()`。
    `progress(阶段, 0~1, 附加文字)`：阶段是 `"download"` / `"extract"`，附加文字是
    「183KB/s，剩4分05秒」这类细节（没有就给空串）。
    """
    folder = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="chart_video_"))
    own = workdir is None

    def report(stage: str) -> Callable[[float, str], None]:
        if progress is None:
            return lambda _fraction, _detail="": None
        return lambda fraction, detail="": progress(stage, fraction, detail)

    try:
        if cancel is not None:
            cancel()
        started = time.time()
        path, duration = download(video_id, folder, cancel=cancel, progress=report("download"))
        if path is None:
            return [], None, 0.0
        if not duration:
            duration = _probe_duration(path)
        if cancel is not None:
            cancel()
        if progress is not None:
            progress("download", 1.0, f"已下载{_size_text(path)}")
        step = min(float(interval or DENSE_STEP), DENSE_STEP)
        logging.info("抽帧开始：%.1f分钟的视频，每%.1f秒采一张（准备抽帧用时%.1f秒）",
                     duration / 60 if duration else 0, step, time.time() - started)
        frames = extract_frames(path, int(max_frames or 400), duration=duration,
                                progress=report("extract"), step=step)
        if progress is not None:
            progress("extract", 1.0, f"抽出{len(frames)}帧")
        return frames, path, duration
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


__all__ = ["ytdlp_available", "ffmpeg_path", "frame_count", "frame_budget", "download",
           "extract_frames", "mean_hash", "video_frames", "prepare_frames", "resample_frames",
           "parse_showinfo_times", "cleanup", "prune_videos", "cache_path_for",
           "MIN_HEIGHT", "FRAME_MAX_EDGE", "DENSE_STEP", "RESAMPLE_STEP"]
