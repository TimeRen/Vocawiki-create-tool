"""GUI 门面：图形界面没启动时，所有问答仍然走终端。

- `available()`：能不能用图形界面（装了 PyQt5 且没传 `--console`）。
- `run(flow)`：拉起主窗口，在后台线程里跑 flow；返回进程退出码。
  flow 返回值若是路径（一般是输出目录），界面会显示「完成」并提供「打开输出文件夹」。
- `ask_response` / `ask_choices` / `ask_multiline`：`utils/helpers.py` 的 prompt_* 在 GUI
  模式下转到这里，调用方（main / vocadb / models.video / upload…）不需要知道自己跑在哪边。
- `open_style_editor` / `open_lyrics_editor` / `open_submit_editor`：三个编辑器标签页的入口
  （utils/color_editor.py 等只负责搬数据与回写模型，界面全在这里）。
- `status()` / `output()`：状态栏文字与「日志」页的一行文字。

线程模型：只有主线程碰控件。工作线程要提问时把请求交给主线程，然后阻塞在自己的 Event 上。
单元测试与终端模式完全不碰 Qt：没调用过 `run()` 时 `is_active()` 一直是 False，
`ask_*` 也永远走不到 Qt 那一段。
"""
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

CONSOLE_FLAG = "--console"
# 环境变量兜底：CI / 单测里不想被 Qt 抢走控制权时设 VOCAWIKI_CONSOLE=1
CONSOLE_ENV = "VOCAWIKI_CONSOLE"

_window = None                       # MainWindow；没跑起来时是 None
_app = None                          # QApplication
_busy = False                        # run() 是否正在跑（含终端回退）


# ---------------------------------------------------------------- 可用性

def console_requested() -> bool:
    """命令行 / 环境变量是否明确要求走终端。"""
    return CONSOLE_FLAG in sys.argv or bool(os.environ.get(CONSOLE_ENV))


def qt_available() -> bool:
    """装了 PyQt5 吗（只看导入，不创建任何 Qt 对象）。"""
    try:
        import PyQt5.QtWidgets  # noqa: F401
    except Exception:                # 没装 / DLL 缺失 / 无显示环境
        return False
    return True


def available() -> bool:
    """能不能用图形界面：有 PyQt5，且没有明确要求走终端。"""
    return not console_requested() and qt_available()


def is_active() -> bool:
    """主窗口是否已经跑起来（`utils/helpers.py` 靠它决定问答走界面还是终端）。"""
    return _window is not None


def main_window():
    """当前主窗口；没有时返回 None。"""
    return _window


# ---------------------------------------------------------------- 启动

def run(flow: Callable[[], Any], title: Optional[str] = None,
        on_done: Optional[Callable[[Any], None]] = None) -> int:
    """拉起主窗口并在后台线程里跑 flow；返回进程退出码。

    图形界面不可用时（没装 PyQt5 / `--console`）直接在**当前线程**跑 flow 并返回 0——
    异常原样抛出，交给调用方按终端模式处理（main.py 里那层 try/except 照旧生效）。
    """
    global _busy
    if not available():
        logging.info("未启用图形界面（未安装 PyQt5 或指定了 %s），按终端模式运行。", CONSOLE_FLAG)
        flow()
        return 0
    if _busy:
        raise RuntimeError("图形界面已经在运行了")
    from utils.ui.window import launch
    _busy = True
    try:
        return launch(flow, title=title, on_done=on_done)
    finally:
        _busy = False


# ---------------------------------------------------------------- 问答

def ask_response(prompt: str, auto_strip: bool = True,
                 validity_checker: Callable[[str], bool] = lambda x: True) -> str:
    """单行回答（界面上是一个输入框 + 「确定」）。"""
    window = _require_window()
    return window.ask_response(prompt, auto_strip=auto_strip, checker=validity_checker)


def ask_choices(prompt: str, choices: Sequence[str], allow_zero: bool = False) -> int:
    """从若干选项里选一个，返回**1 起的编号**（allow_zero 时 0 表示「都不要」）。"""
    window = _require_window()
    return window.ask_choices(prompt, list(choices), allow_zero=allow_zero)


def ask_multiline(prompt: str, auto_strip: bool = True,
                  terminator: Any = None) -> List[str]:
    """整段粘贴后点「完成」，返回按行拆好的文本（空行会被丢弃）。

    terminator 只为实现 `utils/helpers.py` 的同一套签名，界面上不需要（用户自己按「完成」）。
    """
    window = _require_window()
    return window.ask_multiline(prompt, auto_strip=auto_strip)


def _require_window():
    if _window is None:
        raise RuntimeError("图形界面还没启动：请先调用 utils.ui.run()")
    return _window


# ---------------------------------------------------------------- 状态与日志

def status(text: str) -> None:
    """状态栏文字；界面没起来时什么都不做（终端模式已经有日志了）。"""
    if _window is not None:
        _window.set_status(text)


def output(text: str) -> None:
    """往「日志」页写一行；没起来时退回 print。"""
    if _window is not None:
        _window.append_log(text)
    else:
        print(text)


def report_done(output_dir: Any = None) -> None:
    """流程正常结束（界面显示完成提示与「打开输出文件夹」）。"""
    if _window is not None:
        _window.report_done(output_dir)


def report_error(message: str) -> None:
    """流程失败（界面弹提示，日志页保留完整堆栈）。"""
    if _window is not None:
        _window.report_error(message)


def open_folder(path: Any) -> bool:
    """用系统文件管理器打开一个目录 / 定位一个文件。"""
    target = Path(path)
    try:
        if os.name == "nt":
            if target.is_dir():
                os.startfile(str(target))                      # noqa: S606 (Windows API)
            else:
                subprocess.Popen(["explorer", "/select,", str(target)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target.parent if target.is_file() else target)])
        else:
            subprocess.Popen(["xdg-open", str(target.parent if target.is_file() else target)])
        return True
    except OSError as e:
        logging.error("无法打开文件夹 %s：%s", target, e)
        return False


# ---------------------------------------------------------------- 编辑器标签页

def open_style_editor(initial_wiki: str = "", cover_image: Any = None,
                      lyrics_hover: bool = False):
    """打开「样式」标签页，返回用户保存后的各模板颜色（`models.color.ColorEditing`）。

    未保存 / 图形界面不可用时返回 None（调用方视作「没编辑过」）。
    """
    window = _window
    if window is None:
        return None
    from utils.color_editor import parse_color_wiki
    result = window.run_style_editor(initial_wiki, cover_image, lyrics_hover)
    if result is None:
        return None
    text, hover = result
    return parse_color_wiki(text, hover)


def open_lyrics_editor(initial_text: str = "", source_hint: str = "",
                       use_hover: bool = False, use_colors: bool = False,
                       charas: Sequence[str] = ()):
    """打开「歌词」标签页，返回用户确认的 `models.song.Lyrics`；取消时返回 None。"""
    window = _window
    if window is None:
        return None
    from utils.lyrics_editor import LyricsApi
    api = LyricsApi(initial_text, source_hint, use_hover, use_colors, charas)
    return window.run_lyrics_editor(api)


def open_submit_editor(page_name: str, wikitext: str, source_path: Any,
                       ja_name: Optional[str] = None, create_redirect: bool = False,
                       cover: Any = None, family: Any = None,
                       disambig_plan: Any = None) -> bool:
    """打开「提交」标签页；成功挂上界面返回 True（图形界面不可用时 False，调用方自己回退）。"""
    window = _window
    if window is None:
        return False
    from utils.submit_editor import SubmitApi
    api = SubmitApi(page_name, source_path, wikitext, ja_name, create_redirect,
                    cover, family, disambig_plan)
    return bool(window.run_submit_editor(api))
