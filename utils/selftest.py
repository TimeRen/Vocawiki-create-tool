"""打包后的依赖自检（`--selftest-deps <报告路径>`）：确认分发包真的带上了图表要用的东西。

为什么要有它：侧栏「刊」（榜单条目）要用 `yt-dlp` 下载 niconico 视频、用 `imageio-ffmpeg`
自带的那份 ffmpeg 抽帧。这两样都是「拿不到就静默退回浏览器截图」的依赖 —— PyInstaller
漏收某个子模块时程序照样能启动、照样能生成普通条目，只有用户点「刊」才会发现不对。
所以打包脚本打完 exe 后跑一次自检，报告里 `ok` 是 false 就让这次打包失败（见 build.py）。

只写文件、不碰界面：打包出来的是窗口程序（`--windowed`），没有 stdout 可以看。
"""
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Tuple

FLAG = "--selftest-deps"


def requested(argv) -> bool:
    """命令行里有没有 `--selftest-deps`。"""
    return FLAG in list(argv or [])


def report_path(argv) -> Path:
    """报告写到哪里（`--selftest-deps` 后面那个参数；不给就写到当前目录）。"""
    args = list(argv or [])
    try:
        index = args.index(FLAG)
    except ValueError:
        return Path("selftest-deps.json")
    if index + 1 < len(args) and not args[index + 1].startswith("--"):
        return Path(args[index + 1])
    return Path("selftest-deps.json")


def collect() -> Dict[str, object]:
    """查一遍依赖 → 报告字典（`ok` / `problem` / `packages` / `ffmpeg` / `ocr_engine`）。"""
    from utils import chart_video

    packages: Dict[str, str] = {}
    problem: List[str] = []
    try:
        import yt_dlp

        packages["yt_dlp"] = str(getattr(getattr(yt_dlp, "version", None), "__version__", "?"))
    except Exception as error:                   # noqa: BLE001 - 没装就是没装
        problem.append(f"yt-dlp不可用：{error}")
    try:
        import imageio_ffmpeg

        packages["imageio_ffmpeg"] = str(getattr(imageio_ffmpeg, "__version__", "?"))
    except Exception as error:                   # noqa: BLE001
        problem.append(f"imageio-ffmpeg不可用：{error}")
    ffmpeg = chart_video.ffmpeg_path()
    if not ffmpeg:
        problem.append("找不到ffmpeg（imageio-ffmpeg与系统PATH都没有）")
    ocr_engine, ocr_problem = _check_ocr()
    if ocr_problem:
        problem.append(ocr_problem)
    return {
        "ok": not problem,
        "problem": problem,
        "packages": packages,
        "ffmpeg": str(ffmpeg or ""),
        "ocr_engine": ocr_engine,
        "ytdlp_available": chart_video.ytdlp_available(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": sys.version.split()[0],
    }


def _check_ocr() -> Tuple[str, str]:
    """真的构造一次本地 OCR 引擎 → `(引擎名, 问题描述)`。

    只 `find_spec` 是不够的：PyInstaller 很容易漏掉 onnxruntime 的 DLL 或 rapidocr 的
    `models/*.onnx`，那样「装是装了」但一构造就炸，用户点「刊」才发现没 OCR。
    构造一次要 1～3 秒，打包自检里完全值得。
    """
    from utils import chart_ocr

    if not chart_ocr.available():
        return "", "本地OCR不可用（rapidocr未装进包），榜单卡片只能走AI识别"
    try:
        name, engine = chart_ocr.engine()
    except Exception as error:                   # noqa: BLE001 - 构造失败就是没进包
        return "", f"本地OCR引擎构造失败：{error}"
    if not name or engine is None:
        return "", "本地OCR引擎构造失败（模型或onnxruntime没进包）"
    return str(name), ""


def run(argv) -> int:
    """跑自检、写报告、返回退出码（0 = 通过）。"""
    report = collect()
    path = report_path(argv)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        logging.error("写依赖自检报告失败：%s", error)
    for item in report["problem"]:
        logging.error("依赖自检未通过：%s", item)
    return 0 if report["ok"] else 1


__all__ = ["FLAG", "requested", "report_path", "collect", "run"]
