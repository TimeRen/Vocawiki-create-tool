"""通过 pywebview 打开可视化样式编辑器窗口，取回用户编辑后的各模板样式。"""
import base64
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Dict, Optional, Union

from config.config import application_path
from models.color import ColorEditing
from models.song import Song
from utils.string import is_empty

EDITOR_FILE = "css-tag-editor.html"

# 除 Songbox 的三行颜色外，编辑器还同时编辑这两处模板的样式：
#   VOCALOID Songbox Introduction → |lbgcolor（标签格底色/样式，模板自带 background-color: 前缀）
#                                   |ltcolor（标签文字色）
#                                   |rbdcolor（列表格边框色，标签格带额外声明时补上）
#   LyricsKai                     → |lstyle（原文） · |rstyle（译文） · |containerstyle（容器）
# 这三项都是任意 CSS 声明文本，直接写进对应参数即可（模板用 cssText 解析）。
# 编辑器里统一写成「|参数名 = 值」；值为空表示该参数不输出（编辑器里对应「输出」开关关闭）。
TEMPLATE_COLOR_DEFAULTS: Dict[str, str] = {
    "lbgcolor": "#000000",
    "ltcolor": "#ffffff",
    "lstyle": "",
    "rstyle": "",
    "containerstyle": "",
}

_PARAM_RE = re.compile(r"^\|\s*([^\s=|]+)\s*=\s*(.*)$")


def build_initial_color_wiki(song: Song) -> str:
    """编辑器初始内容：Songbox 三行颜色 + Introduction / LyricsKai 已启用的颜色参数。"""
    parts = []
    if song.colors and song.colors.background is not None:
        bg = song.colors.background.to_hex()
        fg = song.colors.text.to_hex()
        parts.append("\n".join(f"|颜色{i} = {bg}\n  color: {fg};" for i in (1, 2, 3)))
    defaults = "\n".join(f"|{key} = {value}"
                         for key, value in TEMPLATE_COLOR_DEFAULTS.items() if value)
    parts.append(defaults)
    return "\n".join(part for part in parts if part)


def _parse_params(text: str) -> Dict[str, str]:
    """把「|参数名 = 值」解析成字典；缩进行视为上一个参数的续行。"""
    params: Dict[str, str] = {}
    current: Optional[str] = None
    for line in (text or "").splitlines():
        match = _PARAM_RE.match(line)
        if match:
            current = match.group(1)
            params[current] = match.group(2).strip()
        elif current and line[:1] in (" ", "\t") and line.strip():
            params[current] = f"{params[current]}\n{line.strip()}".strip()
    return params


def parse_color_wiki(text: str) -> ColorEditing:
    """把编辑器输出按模板归位，得到可直接插入生成流程的各段颜色。"""
    params = _parse_params(text)
    songbox_lines = []
    for i in (1, 2, 3):
        key = f"颜色{i}"
        if key not in params:
            continue
        value_lines = params[key].split("\n")
        songbox_lines.append(f"|{key} = {value_lines[0]}")
        songbox_lines.extend(f"  {line}" for line in value_lines[1:])
    return ColorEditing(
        songbox="\n".join(songbox_lines),
        introduction_bg=params.get("lbgcolor", ""),
        introduction_fg=params.get("ltcolor", ""),
        introduction_border=params.get("rbdcolor", ""),
        lyrics_original=params.get("lstyle", ""),
        lyrics_translated=params.get("rstyle", ""),
        lyrics_background=params.get("containerstyle", ""),
    )


class _EditorApi:
    """暴露给前端 JS 的接口，用于回传编辑结果、提供封面图片、代调 AI 生成 CSS。"""

    def __init__(self, cover_image: Optional[Union[str, Path]] = None):
        self.result: Optional[str] = None
        self._window = None
        self._cover_image: Optional[Path] = Path(cover_image) if cover_image else None

    def save(self, text: str):
        self.result = text
        window = self._window
        self._window = None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass

    def get_cover(self) -> Optional[str]:
        """以 data URI 形式返回封面图片，供编辑器自动载入并取色。"""
        if self._cover_image is None or not self._cover_image.exists():
            return None
        try:
            data = base64.b64encode(self._cover_image.read_bytes()).decode("ascii")
        except OSError as e:
            logging.error("无法读取封面图片：%s", e)
            return None
        mime = mimetypes.guess_type(str(self._cover_image))[0] or "image/jpeg"
        return f"data:{mime};base64,{data}"

    # —— AI 参考封面生成 CSS（密钥在 wiki_credentials.yaml） ——

    def get_ai_context(self) -> dict:
        """编辑器启动时询问：AI 按钮能不能用、用的哪个模型。"""
        try:
            from utils import ai_css
            return ai_css.context()
        except Exception as e:
            logging.debug("读取 AI 配置失败：%s", e)
            return {"enabled": False, "hidden": False, "reason": f"AI 模块不可用：{e}"}

    def ai_generate(self, payload_json: str) -> dict:
        """按封面图生成 CSS。任何异常都转成 {'ok': False, 'error': ...} 回给前端。"""
        try:
            from utils import ai_css
            return ai_css.generate_css(payload_json, self._cover_image)
        except Exception as e:
            logging.error("AI 生成 CSS 失败：%s", e, exc_info=e)
            return {"ok": False, "error": f"AI 生成失败：{e}"}


def open_color_editor(initial_wiki: str = "",
                      cover_image: Optional[Union[str, Path]] = None) -> Optional[ColorEditing]:
    """打开颜色编辑器窗口，返回用户保存的各模板颜色；未保存或不可用时返回 None。

    传入 cover_image 时，编辑器会自动载入封面图片，用户可直接用吸管在封面上取色。
    """
    try:
        import webview
    except ImportError:
        logging.error("未安装 pywebview，无法打开颜色编辑器。请执行 pip install pywebview")
        return None

    html_path = application_path.joinpath(EDITOR_FILE)
    if not html_path.exists():
        logging.error(f"找不到颜色编辑器文件：{html_path}")
        return None

    api = _EditorApi(cover_image)
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
        if api._cover_image is not None:
            window.evaluate_js("window.__vocawikiLoadCover && window.__vocawikiLoadCover();")

    webview.start(func=inject)
    if api.result is None:
        return None
    return parse_color_wiki(api.result)
