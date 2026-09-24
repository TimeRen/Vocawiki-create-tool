"""通过 pywebview 打开可视化颜色编辑器窗口，取回用户编辑后的 Wiki 颜色代码。"""
import json
import logging
from typing import Optional

from config.config import application_path
from models.song import Song
from utils.string import is_empty

EDITOR_FILE = "css-tag-editor.html"


def build_initial_color_wiki(song: Song) -> str:
    """把当前自动取色的结果转成编辑器认识的初始 Wiki 颜色代码。"""
    if not song.colors or song.colors.background is None:
        return ""
    bg = song.colors.background.to_hex()
    fg = song.colors.text.to_hex()
    return "\n".join(
        f"|颜色{i} = {bg}\n  color: {fg};" for i in (1, 2, 3)
    )


class _EditorApi:
    """暴露给前端 JS 的接口，用于回传编辑结果。"""

    def __init__(self):
        self.result: Optional[str] = None
        self._window = None

    def save(self, text: str):
        self.result = text
        window = self._window
        self._window = None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass


def open_color_editor(initial_wiki: str = "") -> Optional[str]:
    """打开颜色编辑器窗口，返回用户保存的 Wiki 颜色代码；未保存或不可用时返回 None。"""
    try:
        import webview
    except ImportError:
        logging.error("未安装 pywebview，无法打开颜色编辑器。请执行 pip install pywebview")
        return None

    html_path = application_path.joinpath(EDITOR_FILE)
    if not html_path.exists():
        logging.error(f"找不到颜色编辑器文件：{html_path}")
        return None

    api = _EditorApi()
    window = webview.create_window(
        "Vocawiki Songbox 颜色编辑器",
        str(html_path),
        js_api=api,
        width=1180,
        height=820,
    )
    api._window = window

    def inject():
        if not is_empty(initial_wiki):
            window.evaluate_js("window.__vocawikiSetWiki(%s);" % json.dumps(initial_wiki))

    webview.start(func=inject)
    return api.result
