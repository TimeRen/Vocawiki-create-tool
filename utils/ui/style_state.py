"""样式编辑器的状态与文本生成（纯逻辑，不依赖 Qt）。

这就是 html/css-tag-editor.html 里那段 JS：一份样式状态 → CSS 声明 → wikitext 参数，
外加反向解析（wikitext / CSS 文本 → 状态）。搬成 Python 是为了能单测，
界面（utils/ui/style_panel.py）只负责读写这里的字典。

三个对象 + 两个全局：

- `blank_state()`：Songbox 三个颜色块（演唱 / P主 / 投稿）各自的样式状态；
  三者各有一份，`gstate` 是「全局」态，编辑全局时同步进三份。
- `tpl_default_states()`：Introduction 标签格 + LyricsKai 容器 / 原文 / 译文。
"""
import logging
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_BG = "#39c5bb"
DEFAULT_FG = "#000000"

RECT_LABELS = ("演唱", "P主", "投稿")
RECT_CLASSES = ("tag-1", "tag-2", "tag-3")

BORDER_STYLES = ("solid", "dashed", "dotted", "double", "none")
FONT_WEIGHTS = (300, 400, 500, 600, 700, 800, 900)

MAX_LAYERS = 5
MAX_STOPS = 8
MAX_BOX_SHADOWS = 5
MAX_TEXT_SHADOWS = 3

# AI 面板能改的属性（与 html 版的 AI_COLOR_PROPS / AI_SONGBOX_PROPS 一致）
AI_COLOR_PROPS = ("color", "background", "background-color", "background-image", "border",
                  "border-color", "box-shadow", "text-shadow", "opacity")
AI_SONGBOX_PROPS = ("background", "color", "font-size", "line-height", "font-weight",
                    "letter-spacing", "border", "border-radius", "box-shadow", "text-shadow",
                    "padding", "opacity")
# 模板对象可用属性（原 JS 的 TPL_TARGETS[*].allow）
TPL_ALLOW = {
    "introLabel": ("background", "background-color", "background-image", "color", "border-radius",
                   "border", "border-color", "box-shadow", "opacity", "padding", "font-size",
                   "font-weight"),
    "lyrContainer": ("background", "background-color", "background-image", "color", "border-radius",
                     "border", "border-color", "box-shadow", "opacity", "padding"),
    "lyrOrig": AI_COLOR_PROPS,
    "lyrTrans": AI_COLOR_PROPS,
}

# 模板目标：key -> {参数名, 段落, 中文名, 是否裸底色, 是否有「输出」开关}
TPL_TARGETS: Dict[str, Dict[str, Any]] = {
    "introLabel": {"param": "lbgcolor", "section": "intro", "label": "标签格",
                   "bare_bg": True, "toggle": False},
    "lyrContainer": {"param": "containerstyle", "section": "lyrics", "label": "容器",
                     "bare_bg": False, "toggle": True},
    "lyrOrig": {"param": "lstyle", "section": "lyrics", "label": "原文",
                "bare_bg": False, "toggle": True},
    "lyrTrans": {"param": "rstyle", "section": "lyrics", "label": "译文",
                 "bare_bg": False, "toggle": True},
}

SECTIONS = {
    "songbox": {"label": "Songbox", "tip": "三个颜色块（演唱 / P主 / 投稿）的底色与文字样式"},
    "intro": {"label": "Introduction", "tip": "{{VOCALOID Songbox Introduction}} 标签格"},
    "lyrics": {"label": "歌词", "tip": "{{LyricsKai}} 容器 / 原文 / 译文"},
}

# 一次「全部（Songbox + Introduction + 歌词）」AI 生成覆盖的对象
AI_ALL_TARGETS = ("songbox", "introLabel", "lyrContainer", "lyrOrig", "lyrTrans")

GOOGLE_GRADIENT_KINDS = ("linear", "radial", "conic")


# ---------------------------------------------------------------- 颜色

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB_RE = re.compile(r"^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,/\s]+([\d.]+%?))?\s*\)$")
# `hsl(210, 20%, 95%)` / `hsl(210 20% 95% / 50%)`：AI 很爱用 hsl 描述配色，
# 以前认不出来就默默地变成白色（用户 2026-09-29 报「AI 生成 Introduction 后 |ltcolor 没变化」）。
_HSL_RE = re.compile(r"^hsla?\(\s*(-?[\d.]+)(?:deg)?\s*[,\s]+([\d.]+)%\s*[,\s]+([\d.]+)%"
                     r"(?:\s*[,/]\s*([\d.]+%?))?\s*\)$")
_NAMED = {
    "white": "#ffffff", "black": "#000000", "red": "#ff0000", "green": "#008000",
    "blue": "#0000ff", "currentcolor": "#5b6cff",
    # 其余常见颜色名（CSS 基本色 + 模型常写的那几个）
    "silver": "#c0c0c0", "gray": "#808080", "grey": "#808080", "maroon": "#800000",
    "yellow": "#ffff00", "olive": "#808000", "lime": "#00ff00", "aqua": "#00ffff",
    "cyan": "#00ffff", "teal": "#008080", "navy": "#000080", "fuchsia": "#ff00ff",
    "magenta": "#ff00ff", "purple": "#800080", "orange": "#ffa500", "pink": "#ffc0cb",
    "brown": "#a52a2a", "gold": "#ffd700", "beige": "#f5f5dc", "ivory": "#fffff0",
    "khaki": "#f0e68c", "salmon": "#fa8072", "tomato": "#ff6347", "violet": "#ee82ee",
    "indigo": "#4b0082", "turquoise": "#40e0d0", "skyblue": "#87ceeb",
    "steelblue": "#4682b4", "lightgray": "#d3d3d3", "lightgrey": "#d3d3d3",
    "darkgray": "#a9a9a9", "darkgrey": "#a9a9a9", "whitesmoke": "#f5f5f5",
    "gainsboro": "#dcdcdc", "lavender": "#e6e6fa", "mistyrose": "#ffe4e1",
    "seashell": "#fff5ee", "cornsilk": "#fff8dc", "lightpink": "#ffb6c1",
    "deepskyblue": "#00bfff", "dodgerblue": "#1e90ff", "royalblue": "#4169e1",
    "slateblue": "#6a5acd", "midnightblue": "#191970", "darkblue": "#00008b",
    "darkred": "#8b0000", "crimson": "#dc143c", "orangered": "#ff4500",
    "darkorange": "#ff8c00", "greenyellow": "#adff2f", "forestgreen": "#228b22",
    "seagreen": "#2e8b57", "darkgreen": "#006400", "mediumseagreen": "#3cb371",
    "lightblue": "#add8e6", "powderblue": "#b0e0e6", "plum": "#dda0dd",
    "orchid": "#da70d6", "mediumpurple": "#9370db", "slategray": "#708090",
    "slategrey": "#708090", "dimgray": "#696969", "dimgrey": "#696969",
}


def norm_hex(value: Any) -> Optional[str]:
    """把 `#RGB` / `#RRGGBB` 归一成小写 `#rrggbb`；非法返回 None。"""
    match = _HEX_RE.match(str(value or "").strip())
    if not match:
        return None
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(char * 2 for char in digits)
    return "#" + digits.lower()


def _hsl_to_hex(hue: float, saturation: float, lightness: float) -> str:
    """`hsl(h, s%, l%)` → `#rrggbb`（CSS 标准算法）。"""
    hue = (hue % 360) / 360
    saturation = max(0.0, min(1.0, saturation / 100))
    lightness = max(0.0, min(1.0, lightness / 100))
    if saturation == 0:
        value = round(lightness * 255)
        return "#%02x%02x%02x" % (value, value, value)
    scale = (lightness * (1 + saturation) if lightness < 0.5
             else lightness + saturation - lightness * saturation)
    base = 2 * lightness - scale

    def channel(offset: float) -> float:
        position = (hue + offset) % 1
        if position < 1 / 6:
            return base + (scale - base) * 6 * position
        if position < 1 / 2:
            return scale
        if position < 2 / 3:
            return base + (scale - base) * (2 / 3 - position) * 6
        return base

    return "#%02x%02x%02x" % tuple(round(max(0.0, min(1.0, channel(offset))) * 255)
                                    for offset in (1 / 3, 0.0, -1 / 3))


def parse_color_or_none(value: Any) -> Optional[Tuple[str, float]]:
    """解析任意 CSS 颜色 → (hex, alpha)；**认不出来返回 None**（调用方决定怎么办）。

    支持 `#rgb` / `#rrggbb` / `#rrggbbaa` / `rgb()` / `rgba()`（逗号或空格分隔、百分比 alpha）/ `hsl()` /
    `hsla()` / 常见颜色名 / `transparent`。
    """
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in ("transparent", "none"):
        return "#ffffff", 0.0
    if text in _NAMED:
        return _NAMED[text], 1.0
    hexed = norm_hex(text)
    if hexed:
        return hexed, 1.0
    if len(text) == 9 and text.startswith("#"):        # #RRGGBBAA
        return norm_hex(text[:7]) or "#ffffff", int(text[7:], 16) / 255
    match = _RGB_RE.match(text)
    if match:
        red, green, blue = (max(0, min(255, round(float(match.group(i))))) for i in (1, 2, 3))
        alpha = match.group(4)
        if alpha is None:
            return "#%02x%02x%02x" % (red, green, blue), 1.0
        alpha_value = float(alpha[:-1]) / 100 if alpha.endswith("%") else float(alpha)
        return "#%02x%02x%02x" % (red, green, blue), max(0.0, min(1.0, alpha_value))
    match = _HSL_RE.match(text)
    if match:
        hexed = _hsl_to_hex(float(match.group(1)), float(match.group(2)), float(match.group(3)))
        alpha = match.group(4)
        if alpha is None:
            return hexed, 1.0
        alpha_value = float(alpha[:-1]) / 100 if alpha.endswith("%") else float(alpha)
        return hexed, max(0.0, min(1.0, alpha_value))
    return None


def parse_color(value: Any) -> Tuple[str, float]:
    """解析任意 CSS 颜色 → (hex, alpha)。认不出来就当白色、不透明。

    ⚠️ 写状态请用 `parse_color_or_none()`：认不出来的值当白色会把用户原来的颜色抹掉。
    """
    return parse_color_or_none(value) or ("#ffffff", 1.0)


def css_color(hex_value: Any, alpha: float = 1.0) -> str:
    """(hex, alpha) → CSS 颜色文本：`#rrggbb` / `rgba(r, g, b, a)` / `transparent`。"""
    color = norm_hex(hex_value) or "#ffffff"
    alpha = 1.0 if alpha is None else float(alpha)
    if alpha >= 1:
        return color
    if alpha <= 0:
        return "transparent"
    red, green, blue = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red}, {green}, {blue}, {alpha:.2f})"


def perceived_lightness(hex_value: Any) -> float:
    """相对亮度（照搬 models.color.Color.perceived_lightness 的算法）。"""
    color = norm_hex(hex_value) or "#000000"
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]

    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    luminance = (0.2126 * linear(channels[0]) + 0.7152 * linear(channels[1])
                 + 0.0722 * linear(channels[2]))
    if luminance <= 216 / 24389:
        return luminance * (24389 / 27)
    return luminance ** (1 / 3) * 116 - 16


def auto_text_color(bg_hex: Any, threshold: float = 60) -> str:
    """按底色亮度挑黑字还是白字（阈值默认 60）。"""
    return DEFAULT_FG if perceived_lightness(bg_hex) >= float(threshold) else "#ffffff"


# ---------------------------------------------------------------- 状态

def blank_state() -> Dict[str, Any]:
    """Songbox 颜色块的一份默认样式（对应 JS 的 blankState）。"""
    return {
        "text": "",
        "width": 100, "maxWidth": 450, "height": 24, "padX": 12, "padY": 0,
        "radius": 10, "alignCenter": True, "boxSizing": True,
        "color": DEFAULT_FG, "colorAlpha": 1.0,
        "fontSize": 12, "lineHeight": 1.0, "weight": 700, "letterSpacing": 0.0,
        "opacity": 1.0,
        "bgSolid": DEFAULT_BG, "bgSolidAlpha": 1.0, "bgLayers": [], "bgSet": False,
        "borderWidth": 2, "borderStyle": "solid", "borderCurrent": True,
        "borderColor": DEFAULT_FG, "borderAlpha": 1.0,
        "boxShadows": [], "textShadows": [], "extras": [],
        "force": set(),
    }


def copy_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """深拷贝一份状态（force 是 set，单独复制）。"""
    clone: Dict[str, Any] = {}
    for key, value in state.items():
        if key == "force":
            clone[key] = set(value)
        elif key in ("bgLayers", "boxShadows", "textShadows", "extras"):
            clone[key] = [dict(item) if isinstance(item, dict) else item for item in value]
        else:
            clone[key] = value
    return clone


def tpl_default_states() -> Dict[str, Dict[str, Any]]:
    """四个模板目标的默认样式（对应 JS 的 tplDefaultState）。

    ⚠️ 歌词那三项按**模板的真实默认**来，别沿用了 Songbox 的默认（黑边框 + 圆角）：
    `.Lyrics` 只有 `display:flow-root; min-width:480px`，`.Lyrics-original` /
    `.Lyrics-translated` 只有 `width:49.85%; display:inline-block`（实测 voca.wiki）——
    都没有底色 / 边框 / 圆角，文字还是左对齐的。默认值要是画成带黑边框的圆角块，
    预览就会显示一条 wikitext 里根本不存在的边框（用户 2026-10-16 报）。
    """
    states: Dict[str, Dict[str, Any]] = {}
    for key, spec in TPL_TARGETS.items():
        state = blank_state()
        state["bgSet"] = False
        # 只有 containerstyle / lstyle / rstyle 有「输出」开关；lbgcolor / ltcolor 恒输出
        state["enabled"] = not spec["toggle"]
        if key == "introLabel":
            state.update(bgSolid="#000000", color="#ffffff", bgSet=False, enabled=True)
        elif key == "lyrContainer":
            # 白底（≈ 站上的页面底色）、无边框、直角、无内边距
            state.update(bgSolid="#ffffff", color="#000000", enabled=False,
                         borderWidth=0, radius=0, padX=0, padY=0)
        else:
            # 原文 / 译文：没有底色（透明，透出容器），无边框，文字左对齐、无内缩
            state.update(bgSolid="#ffffff", bgSolidAlpha=0.0, color="#000000", enabled=False,
                         borderWidth=0, radius=0, padX=0, padY=0, alignCenter=False)
        state["text"] = spec["label"]
        states[key] = state
    return states


# 站上 `.Lyrics-original` / `.Lyrics-translated` 会从 `.Lyrics`（`containerstyle`）继承的
# 文字属性 → 状态里的字段名。`lstyle` / `rstyle` 没写同名声明时，条目正文用的是容器的值。
INHERITED_TEXT_FIELDS = {"color": "color", "font-size": "fontSize", "font-weight": "weight",
                         "letter-spacing": "letterSpacing", "text-shadow": "textShadows"}


def resolve_inherited_states(tpl_states: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """预览用的一份状态：歌词原文 / 译文没自己设文字样式时，跟着容器（`containerstyle`）走。

    站上 `.Lyrics-original` / `.Lyrics-translated` 的 CSS（Template:LyricsKai/styles.css）
    只写了 `width` / `display` / `white-space` / `line-height`，**没有** `color` 与字号 ——
    `lstyle` / `rstyle` 没写就继承 `.Lyrics` 的这些属性。所以只把「容器」的文字颜色改成
    白色时，条目里的歌词正文确实是白字。预览原来各画各的（原文 / 译文永远用自己那份默认
    黑字），于是**把文字设成白色、预览里还是黑的**（用户 2026-10-22 报）。

    「有没有自己设过」按 `delta_decls()` 同一口径判断：与默认值相同、又不是 AI / 从文本
    载入强制写过的属性，`delta_decls()` 会省掉 —— 也就等于没设过。
    没有需要继承的项时**原样返回**（对象还是那几份，别的地方按 id 判断的用法不受影响）。
    """
    container = tpl_states.get("lyrContainer")
    if not container or not container.get("enabled"):
        return tpl_states
    defaults = tpl_default_states()
    container_decls = dict(build_decls(container, with_background=False))
    resolved = dict(tpl_states)
    changed = False
    for key in ("lyrOrig", "lyrTrans"):
        state = tpl_states.get(key)
        if state is None:
            continue
        default = defaults.get(key) or {}
        forced = set(state.get("force") or ())
        decls = dict(build_decls(state, with_background=False))
        default_decls = dict(build_decls(default, with_background=False))
        inherited = {field: container.get(field) for prop, field in INHERITED_TEXT_FIELDS.items()
                     if prop not in forced
                     and decls.get(prop) == default_decls.get(prop)
                     and container_decls.get(prop) != default_decls.get(prop)}
        if not inherited or all(state.get(field) == value for field, value in inherited.items()):
            continue                              # 没有要继承的（或已经一样）
        clone = copy_state(state)
        clone.update(inherited)
        if "color" in inherited:
            clone["colorAlpha"] = container.get("colorAlpha", 1.0)
        resolved[key] = clone
        changed = True
    return resolved if changed else tpl_states


def default_layer(kind: str = "linear") -> Dict[str, Any]:
    """新增一层渐变时的默认值（青绿系，与模板默认底色同一色系）。"""
    return {"kind": kind, "repeat": False, "hard": False, "angle": 135,
            "angleUnit": "deg", "dir": "", "headSet": True,
            "cx": 50, "cy": 50, "cxUnit": "%", "cyUnit": "%",
            "shape": "circle", "sizeUnit": "percent",
            "sx": 60, "sy": 40, "sizeKw": "",
            "radiusX": 0, "radiusUnitX": "", "radiusY": 0, "radiusUnitY": "",
            "fromAngle": 0, "fromUnit": "deg",
            "posSet": False, "sizeSet": False, "repeatMode": "", "origin": "", "clip": "",
            "stops": [{"color": DEFAULT_BG, "alpha": 1.0, "pos": 0, "unit": "%", "aa": False},
                      {"color": "#6fe3da", "alpha": 1.0, "pos": 100, "unit": "%", "aa": False}]}


def default_box_shadow() -> Dict[str, Any]:
    return {"x": 0, "y": 2, "blur": 6, "spread": 0, "color": "#0f172a", "alpha": 0.25,
            "inset": False}


def default_text_shadow() -> Dict[str, Any]:
    return {"x": 0, "y": 1, "blur": 2, "color": "#000000", "alpha": 0.5}


# ---------------------------------------------------------------- CSS 值

def _num(value: Any, fallback: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _trim(value: float) -> str:
    """去掉小数尾巴：1.0 → 1，0.25 → 0.25。"""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _stop_css(stop: Dict[str, Any], pos: float) -> List[str]:
    """一个色标（含「硬边 / 抗锯齿 / 颜色提示」）在给定位置上的文本。"""
    unit = stop.get("unit") or "%"
    position = f"{_trim(pos)}{unit}"
    if stop.get("hint"):
        return [position]                      # 颜色提示：只有位置，没有颜色
    color = css_color(stop.get("color"), stop.get("alpha", 1.0))
    if stop.get("aa"):
        if unit == "%":
            return [f"{color} calc({_trim(pos)}% - 1px)", f"{color} calc({_trim(pos)}% + 1px)"]
        return [f"{color} {position}"]
    return [f"{color} {position}"]


def _stops_css(layer: Dict[str, Any]) -> List[str]:
    """整层色标 → 逗号之间的文本片段（硬边时把前一个色标再写一遍）。"""
    stops = list(layer.get("stops") or [])
    parts: List[str] = []
    for index, stop in enumerate(stops):
        pos = _num(stop.get("pos"))
        if index and layer.get("hard") and not stop.get("hint"):
            parts.extend(_stop_css(stops[index - 1], pos))
        parts.extend(_stop_css(stop, pos))
    return parts


def _position_css(layer: Dict[str, Any]) -> str:
    """背景块位置（`10px 10px` / `center 10px`）；没设过返回空串。"""
    if not layer.get("posSet"):
        return ""
    def one(value: Any, unit: Any) -> str:
        unit = str(unit or "%")
        if unit == "%" and _num(value) == 0:
            return "0%"
        return f"{_trim(_num(value))}{unit}"
    return f"{one(layer.get('posX'), layer.get('posUnitX'))} " \
           f"{one(layer.get('posY'), layer.get('posUnitY'))}"


def _size_css(layer: Dict[str, Any]) -> str:
    """背景块尺寸（`20px 20px` / `auto 50px` / `cover`）；没设过返回空串。"""
    if not layer.get("sizeSet"):
        return ""
    unit_w = str(layer.get("sizeUnitW") or "auto")
    unit_h = str(layer.get("sizeUnitH") or "auto")
    if unit_w in ("cover", "contain"):
        return unit_w
    def one(value: Any, unit: str) -> str:
        return unit if unit == "auto" else f"{_trim(_num(value))}{unit}"
    return f"{one(layer.get('sizeW'), unit_w)} {one(layer.get('sizeH'), unit_h)}"


def layer_tail_css(layer: Dict[str, Any]) -> str:
    """背景块尾部的参数文本：`<position>[/<size>] [repeat] [origin] [clip]`。"""
    position = _position_css(layer)
    size = _size_css(layer)
    parts: List[str] = []
    if position:
        parts.append(f"{position}/{size}" if size else position)
    elif size:
        parts.append(f"0% 0%/{size}")
    if layer.get("repeatMode"):
        parts.append(str(layer["repeatMode"]))
    if layer.get("origin"):
        parts.append(str(layer["origin"]))
    if layer.get("clip"):
        parts.append(str(layer["clip"]))
    return " ".join(parts)


def gradient_css(layer: Dict[str, Any]) -> str:
    """一层渐变 → `[repeating-]linear-gradient(...)`（不再带背景块尾部参数，见 `layer_tail_css`）。"""
    stops: List[Dict[str, Any]] = list(layer.get("stops") or [])
    if len(stops) < 2:
        return ""
    parts = _stops_css(layer)
    prefix = "repeating-" if layer.get("repeat") else ""
    kind = layer.get("kind") or "linear"
    if kind == "radial":
        head = _radial_head_css(layer)
        return f"{prefix}radial-gradient({head}, {', '.join(parts)})"
    if kind == "conic":
        head = _conic_head_css(layer)
        return f"{prefix}conic-gradient({head}{', ' if head else ''}{', '.join(parts)})"
    direction = str(layer.get("dir") or "")
    if direction:
        return f"{prefix}linear-gradient({direction}, {', '.join(parts)})"
    unit = str(layer.get("angleUnit") or "deg")
    angle = _trim(_num(layer.get("angle")))
    return f"{prefix}linear-gradient({angle}{unit}, {', '.join(parts)})"


def _radial_head_css(layer: Dict[str, Any]) -> str:
    """`circle closest-side at 75px 25px` / `75px 100px at …` / `circle at …`。"""
    shape = layer.get("shape") or "circle"
    size_kw = str(layer.get("sizeKw") or "").strip()
    radius_x = layer.get("radiusX")
    radius_y = layer.get("radiusY")
    unit_x = str(layer.get("radiusUnitX") or "")
    unit_y = str(layer.get("radiusUnitY") or "")
    head_parts: List[str] = []
    if layer.get("headSet") is False:
        # 原来的写法没有头部（`radial-gradient(a,b,c)`）→ 只补一个默认形状，
        # 不能凭空加上 `60% 40%` 这种尺寸，否则写回站点后渲染就变了
        head_parts.append(shape if shape in ("circle", "ellipse") else "ellipse")
    elif size_kw:
        head_parts.append(f"{shape} {size_kw}")
    elif unit_x and _num(radius_x) > 0:
        if shape == "ellipse" and unit_y and _num(radius_y) > 0:
            head_parts.append(f"{_trim(_num(radius_x))}{unit_x} {_trim(_num(radius_y))}{unit_y}")
        else:
            head_parts.append(f"{shape} {_trim(_num(radius_x))}{unit_x}")
    elif ("circle" in (layer.get("shape") or "circle")
          and (layer.get("sizeUnit") or "percent") != "percent"):
        head_parts.append(shape)
    elif shape == "ellipse":
        head_parts.append(f"ellipse {_trim(_num(layer.get('sx'), 60))}% "
                          f"{_trim(_num(layer.get('sy'), 40))}%")
    else:
        head_parts.append("circle")
    position = _at_css(layer)
    return " ".join(part for part in [" ".join(head_parts), position] if part)


def _at_css(layer: Dict[str, Any]) -> str:
    """`at 50% 50%`（圆心）；没设过就按默认 50% 50%。"""
    unit_x = str(layer.get("cxUnit") or "%")
    unit_y = str(layer.get("cyUnit") or "%")
    cx, cy = _trim(_num(layer.get("cx"), 50)), _trim(_num(layer.get("cy"), 50))
    return f"at {cx}{unit_x} {cy}{unit_y}"


def _conic_head_css(layer: Dict[str, Any]) -> str:
    """`from 135deg at 50% 50%`（`from` 角度与线性共用 `angle` 字段）。"""
    unit = str(layer.get("angleUnit") or "deg")
    angle = _trim(_num(layer.get("angle")))
    return f"from {angle}{unit} {_at_css(layer)}"


def background_value(state: Dict[str, Any], bold_first: bool = True) -> str:
    """`background` 的值：渐变层（index 0 最上）在上，纯底色垫底。"""
    layers = []
    for layer in state.get("bgLayers") or []:
        text = gradient_css(layer)
        if not text:
            continue
        tail = layer_tail_css(layer)
        layers.append(f"{text} {tail}" if tail else text)
    layers = [item for item in layers if item]
    solid = css_color(state.get("bgSolid"), state.get("bgSolidAlpha", 1.0))
    if layers:
        return ", ".join([*layers, solid])
    return solid


def box_shadow_css(shadows: Sequence[Dict[str, Any]]) -> str:
    parts = []
    for item in shadows:
        color = css_color(item.get("color"), item.get("alpha", 1.0))
        prefix = "inset " if item.get("inset") else ""
        parts.append(f"{prefix}{_trim(_num(item.get('x')))}px {_trim(_num(item.get('y')))}px "
                     f"{_trim(_num(item.get('blur')))}px {_trim(_num(item.get('spread')))}px {color}")
    return ", ".join(parts)


def text_shadow_css(shadows: Sequence[Dict[str, Any]]) -> str:
    parts = []
    for item in shadows:
        color = css_color(item.get("color"), item.get("alpha", 1.0))
        parts.append(f"{_trim(_num(item.get('x')))}px {_trim(_num(item.get('y')))}px "
                     f"{_trim(_num(item.get('blur')))}px {color}")
    return ", ".join(parts)


def build_decls(state: Dict[str, Any], with_background: bool = True) -> List[Tuple[str, str]]:
    """状态 → 有序的 CSS 声明列表（顺序与 html 版 buildDecls 一致）。"""
    decls: List[Tuple[str, str]] = [("display", "flex"), ("justify-content", "center")]
    if state.get("alignCenter"):
        decls.append(("align-items", "center"))
    if state.get("width"):
        decls.append(("width", f"{_trim(_num(state.get('width')))}%"))
    if state.get("maxWidth"):
        decls.append(("max-width", f"{_trim(_num(state.get('maxWidth')))}px"))
    if state.get("height"):
        decls.append(("height", f"{_trim(_num(state.get('height')))}px"))
    decls.append(("padding", f"{_trim(_num(state.get('padY')))}px {_trim(_num(state.get('padX')))}px"))
    decls.append(("font-size", f"{_trim(_num(state.get('fontSize')))}px"))
    decls.append(("line-height", _trim(_num(state.get("lineHeight"), 1))))
    decls.append(("border", _border_value(state)))
    decls.append(("border-radius", f"{_trim(_num(state.get('radius')))}px"))
    decls.append(("font-weight", str(int(_num(state.get("weight"), 400)))))
    decls.append(("color", css_color(state.get("color"), state.get("colorAlpha", 1.0))))
    if with_background:
        decls.append(("background", background_value(state)))
    if _num(state.get("letterSpacing")):
        decls.append(("letter-spacing", f"{_trim(_num(state.get('letterSpacing')))}px"))
    shadows = box_shadow_css(state.get("boxShadows") or [])
    if shadows:
        decls.append(("box-shadow", shadows))
    text_shadows = text_shadow_css(state.get("textShadows") or [])
    if text_shadows:
        decls.append(("text-shadow", text_shadows))
    if abs(_num(state.get("opacity"), 1) - 1) > 1e-6:
        decls.append(("opacity", _trim(_num(state.get("opacity"), 1))))
    decls.append(("box-sizing",
                  "border-box" if state.get("boxSizing", True) else "content-box"))
    for extra in state.get("extras") or []:
        text = str(extra).strip().rstrip(";")
        if not text:
            continue
        if ":" in text:
            prop, value = text.split(":", 1)
            decls.append((prop.strip(), value.strip()))
        else:
            decls.append((text, ""))                  # 写坏的行原样带出去
    return decls


def _border_value(state: Dict[str, Any]) -> str:
    width = _num(state.get("borderWidth"))
    style = state.get("borderStyle") or "solid"
    if width <= 0 or style == "none":
        return "0"
    color = ("currentColor" if state.get("borderCurrent", True)
             else css_color(state.get("borderColor"), state.get("borderAlpha", 1.0)))
    return f"{_trim(width)}px {style} {color}"


def css_text(decls: Sequence[Tuple[str, str]], delimiter: str = "\n  ") -> str:
    """声明列表 → CSS 文本（跳过只有属性名没有值的容错项）。"""
    return delimiter.join(f"{prop}: {value};" for prop, value in decls if value != "")


def code_css(selector: str, state: Dict[str, Any], comment: str = "") -> str:
    """`/* 演唱 */\\n.tag-1 {\\n  display: flex;\\n  …\\n}`（预览用代码框）。"""
    body = "".join(f"  {prop}: {value};\n" for prop, value in build_decls(state) if value != "")
    head = f"/* {comment} */\n" if comment else ""
    return f"{head}{selector} {{\n{body}}}"


# ---------------------------------------------------------------- wikitext

def _baseline(state: Dict[str, Any]) -> Dict[str, Any]:
    """比较基线：模板对象用各自的默认值，Songbox 颜色块用 blank_state()。"""
    return blank_state()


def delta_decls(state: Dict[str, Any], default: Optional[Dict[str, Any]] = None,
                with_background: bool = True) -> List[Tuple[str, str]]:
    """只保留与默认样式不同的声明（避免把没改过的东西写进 wikitext）。"""
    default = default or _baseline(state)
    base = dict(build_decls(default, with_background=with_background))
    forced = set(state.get("force") or ())
    result: List[Tuple[str, str]] = []
    seen = set()
    for prop, value in build_decls(state, with_background=with_background):
        if prop in seen:
            continue
        seen.add(prop)
        if value == "":
            continue
        if prop in forced or base.get(prop) != value:
            result.append((prop, value))
    return result


def wiki_decls(state: Dict[str, Any], default: Optional[Dict[str, Any]] = None,
               with_background: bool = True) -> List[Tuple[str, str]]:
    """`|颜色N =` 那一行用的声明：background 提到最前面。"""
    decls = delta_decls(state, default, with_background=with_background)
    backgrounds = [item for item in decls if item[0] == "background"]
    rest = [item for item in decls if item[0] != "background"]
    return [*backgrounds, *rest]


def songbox_wiki_text(states: Sequence[Dict[str, Any]]) -> str:
    """三个 `|颜色1/2/3` 参数块（始终同时输出，与当前编辑对象无关）。"""
    blocks = []
    for index, state in enumerate(states, start=1):
        decls = wiki_decls(state)
        if not decls:
            blocks.append(f"|颜色{index} = ")
            continue
        head_prop, head_value = decls[0]
        first = f"{head_value};" if head_prop == "background" else f"{head_prop}: {head_value};"
        rest = "".join(f"\n  {prop}: {value};" for prop, value in decls[1:])
        blocks.append(f"|颜色{index} = {first}{rest}")
    return "\n".join(blocks)


def tpl_css_text(state: Dict[str, Any], default: Optional[Dict[str, Any]] = None) -> str:
    """歌词三项的值：`background: …; color: …; 额外声明;`（用空格连接）。

    底色只在「真正设过」（bgSet）时写出：设成模板默认色也要写，否则用户的意图会丢；
    没碰过底色就不写，免得把默认底色硬塞进代码里。
    """
    with_bg = bool(state.get("bgSet"))
    decls = delta_decls(state, default, with_background=with_bg)
    if with_bg and not any(prop == "background" for prop, _value in decls):
        decls.insert(0, ("background", background_value(state)))
    return " ".join(f"{prop}: {value};" for prop, value in decls if value != "")


# 标签格能跟着 lbgcolor / ltcolor 一起写出去的属性。
# 模板里这两个参数分别塞进 `background-color: {{{lbgcolor}}}` 与 `color: {{{ltcolor}}}`，
# 所以先写颜色、后面接声明就能照常生效（实测站内写法：`|lbgcolor = #fff;
# background:linear-gradient(30deg, …)`、`|ltcolor = #f7daf5;text-shadow:0 0 3px #7d99ce`）。
# 盒子相关的跟 lbgcolor、文字相关的跟 ltcolor（参 涅槃(HotaRu) 的 `|lbgcolor = #4a3e4d;
# padding: 6px 12px; border-radius: 4px 0 0 4px; box-shadow: …` / `|ltcolor = #ffffff; … font-weight: bold`）。
INTRO_BOX_PROPS = ("padding", "border", "border-radius", "box-shadow", "opacity")
INTRO_TEXT_PROPS = ("font-size", "font-weight", "letter-spacing", "text-shadow")
# 这两个参数自己就是颜色，其余属性不用重复写（display / width / box-sizing 对 `<td>` 也没意义）
INTRO_SKIP_PROPS = ("color", "background", "background-color", "display", "justify-content",
                    "align-items", "width", "max-width", "height", "box-sizing",
                    "line-height")


def intro_decls(state: Dict[str, Any], props: Sequence[str],
                default: Optional[Dict[str, Any]] = None) -> List[Tuple[str, str]]:
    """标签格里属于 props 的那几条声明（和默认样式一样的不写）。

    「额外声明」（`extras`）不在这里 —— 它们是用户手写的，由 `tpl_wiki_text` 原样接在后面。
    """
    default = default or tpl_default_states()["introLabel"]
    without_extras = copy_state(state)
    without_extras["extras"] = []
    decls = delta_decls(without_extras, default, with_background=False)
    # extras 里已经写过的属性不再由状态重复给一份（例如四值圆角 `4px 0 0 4px` 只能留在 extras）
    in_extras = {str(item).split(":", 1)[0].strip()
                 for item in state.get("extras") or [] if ":" in str(item)}
    return [(prop, value) for prop, value in decls
            if prop in props and prop not in INTRO_SKIP_PROPS and prop not in in_extras
            and value != ""]


def _close_list_border(intro: Dict[str, Any], box: List[str]) -> None:
    """给 `|lbgcolor` 的声明末尾补一条「只用颜色」的 `border: <色>`（就地改 `box`）。

    模板里列表格写的是 `border: {{{rbdcolor|{{{lbgcolor}}}}}} 1px solid;`：
    不写 `|rbdcolor` 时它会拿**整串 lbgcolor** 当边框色，而末尾那条声明会被补上的
    ` 1px solid` 拼成一句 —— 于是

      * `#4a3e4d; padding: 6px 12px` → `border: #4a3e4d; padding: 6px 12px 1px solid;`
        最后一条（padding）非法被丢掉，`border: #4a3e4d` 没有线型 → **列表格没有边框**。

    所以让末尾留一条纯色 border：模板补完就是 `border: <色> 1px solid` ✓，
    **不必多写 `|rbdcolor` 参数**（用户 2026-09-29 要的「两全法」）。
       * 标签格那边这条没有宽度/线型 → 本来就不显形；真要边框时用它的颜色，
         而且声明里已有的 `border: …` 排在这条前面 → 标签格仍按 AI / 用户给的粗细与线型来。
       * 抠不出颜色的兜底用底色（与模板默认的 rbdcolor=lbgcolor 一致）。
    只在盒子那串**非空**时才补：纯色 `|lbgcolor = #4a3e4d` 时模板本来就补得出来，不用画蛇添足。
    """
    if not box:
        return
    bg = css_color(intro.get("bgSolid"), intro.get("bgSolidAlpha", 1.0))
    border = css_color(intro.get("borderColor"), intro.get("borderAlpha", 1.0))
    has_border = any(item.lower().startswith("border:") for item in box)
    if has_border and not box[-1].lower().startswith("border:"):
        # 末尾不是 border 的话，前面的 `border: …` 会被原样保留 → 列表格拿它当边框，正好
        return
    box.append(f"border: {border if has_border else bg}")


def tpl_wiki_text(states: Dict[str, Dict[str, Any]]) -> str:
    """`|lbgcolor` / `|ltcolor` / `|rbdcolor`（**原文有才写**）/ 歌词三项（顺序固定）。

    ⚠️ 标签格这里以前只写「底色 + 「额外声明」框里的东西」与文字色：模型里已经有的
    padding / border-radius / border / font-size / font-weight / box-shadow 全被丢掉，
    于是 AI 生成的那一大串 CSS 里只有两个颜色进得了 wikitext（用户 2026-09 报
    「AI 生成 Introduction 的颜色毫无变化」就是这个）。现在按盒子 / 文字分两拨一起写出去。

    ⚠️ `|rbdcolor`（列表格边框色）**只在该参数本来就存在时才写**（用户 2026-09-29 要的：
    「这次是多 rbdcolor」—— 自动补出来的那一行是多余参数）。原文里它就是底色（也就是
    我们以前自动补的那种）时跟着新底色同步，用户特意设了别的颜色则原样保留。
    """
    lines: List[str] = []
    intro = states["introLabel"]
    default = tpl_default_states()["introLabel"]
    extras = [str(item).strip().rstrip(";") for item in intro.get("extras") or [] if str(item).strip()]
    layers = [gradient_css(layer) for layer in intro.get("bgLayers") or []]
    layers = [item for item in layers if item]
    box = list(extras)
    if layers:
        box.insert(0, f"background-image: {', '.join(layers)}")
    box = [f"{prop}: {value}" for prop, value in intro_decls(intro, INTRO_BOX_PROPS, default)] + box
    _close_list_border(intro, box)
    value = "; ".join([css_color(intro.get("bgSolid"), intro.get("bgSolidAlpha", 1.0)), *box])
    lines.append(f"|lbgcolor = {value}")
    text = [f"{prop}: {value}" for prop, value
            in intro_decls(intro, INTRO_TEXT_PROPS, default)]
    lines.append("|ltcolor = " + "; ".join(
        [css_color(intro.get("color"), intro.get("colorAlpha", 1.0)), *text]))
    border = str(intro.get("rbdcolor") or "").strip()
    if border:
        if intro.get("rbdFollowsBg"):
            border = css_color(intro.get("bgSolid"), intro.get("bgSolidAlpha", 1.0))
        lines.append(f"|rbdcolor = {border}")
    defaults = tpl_default_states()
    for key in ("lyrContainer", "lyrOrig", "lyrTrans"):
        state = states[key]
        if not state.get("enabled"):
            continue
        lines.append(f"|{TPL_TARGETS[key]['param']} = {tpl_css_text(state, defaults[key])}")
    return "\n".join(lines)


def full_wiki_text(states: Sequence[Dict[str, Any]], tpl_states: Dict[str, Dict[str, Any]]) -> str:
    """编辑器「保存」时回传的完整参数文本（三段固定顺序拼接）。"""
    return songbox_wiki_text(states) + "\n" + tpl_wiki_text(tpl_states)


# ---------------------------------------------------------------- 反向解析

_PARAM_RE = re.compile(r"^\|\s*([^\s=|]+)\s*=\s*(.*)$")
_PROP_RE = re.compile(r"^-{0,2}[a-zA-Z][a-zA-Z0-9-]*$")


def split_params(text: str) -> Dict[str, str]:
    """把「|参数 = 值」+ 缩进续行拆成 {参数: 值文本}。"""
    params: Dict[str, str] = {}
    current: Optional[str] = None
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        match = _PARAM_RE.match(line)
        if match:
            current = match.group(1)
            params[current] = match.group(2).strip()
        elif current and line[:1] in (" ", "\t") and line.strip():
            params[current] = f"{params[current]}\n{line.strip()}".rstrip()
    return params


# 编辑器写出去 / 认得回来的参数名（`|rbdcolor` 只在原文本来就有时才写回去，不算新参数）
WIKI_PARAMS = ("颜色1", "颜色2", "颜色3", "lbgcolor", "ltcolor") + tuple(
    spec["param"] for spec in TPL_TARGETS.values() if spec["param"] != "lbgcolor")


def has_known_params(text: str) -> bool:
    """这段文本里有没有我们认识的参数（`|颜色1 = …` / `|lstyle = …`）。

    左下角那个框上的「从文本载入」用它当闸门：空框 / 贴错东西时直接拒绝，
    免得拿一堆默认值把用户刚调好的模型盖掉（那看起来就像「全被重置了」）。
    """
    params = split_params(text)
    return any(name in params for name in WIKI_PARAMS)


def parse_decl_text(text: str) -> List[Tuple[str, str]]:
    """`prop: value; prop: value;` → [(prop, value)]；裸值用 background 补属性名。

    行尾分号可有可无，换行与分号都当分隔符（wikitext 里两个声明可能写在同一行）。
    """
    decls: List[Tuple[str, str]] = []
    for chunk in re.split(r"[;\n]+", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            prop, value = chunk.split(":", 1)
            if _PROP_RE.match(prop.strip()):
                decls.append((prop.strip().lower(), value.strip()))
                continue
            decls.append(("background", chunk))          # 含冒号的裸值（如 linear-gradient 里的 at 50%）
            continue
        decls.append(("background", chunk))
    return decls


def apply_decls(state: Dict[str, Any], decls: Sequence[Tuple[str, str]],
                bare_background: bool = True) -> Dict[str, Any]:
    """把解析出来的声明写进状态（认不出的属性落到 extras，保证输出不丢东西）。"""
    for prop, value in decls:
        lower = prop.lower()
        if lower in ("display", "justify-content"):
            continue
        if lower == "align-items":
            state["alignCenter"] = value.strip().lower() == "center"
        elif lower == "width":
            state["width"] = _clamp(_length(value, "%"), 20, 100)
        elif lower == "max-width":
            state["maxWidth"] = _clamp(_length(value, "px"), 120, 760)
        elif lower == "height":
            state["height"] = _clamp(_length(value, "px"), 16, 80)
        elif lower == "padding":
            parts = [item for item in re.split(r"\s+", value.strip()) if item]
            if len(parts) >= 2:
                state["padY"] = _clamp(_length(parts[0], "px"), 0, 30)
                state["padX"] = _clamp(_length(parts[1], "px"), 0, 60)
            elif parts:
                state["padX"] = _clamp(_length(parts[0], "px"), 0, 60)
        elif lower == "font-size":
            state["fontSize"] = _clamp(_length(value, "px"), 8, 32)
        elif lower == "line-height":
            state["lineHeight"] = _clamp(_number(value), 0.8, 2.6)
        elif lower == "border-radius":
            parts = [item for item in re.split(r"\s+", value.strip()) if item]
            if len(parts) > 1:
                # 四个角分开写（`4px 0 0 4px`）——状态里只有一个圆角，原样留给 extras，免得被简写
                state["extras"].append(f"{prop}: {value}")
            else:
                state["radius"] = _clamp(_length(value, "px"), 0, 100)
                _drop_extra(state, prop)      # 单个值能用状态表示，手写的旧值让位（后写覆盖先写）
        elif lower == "font-weight":
            state["weight"] = _weight(value)
        elif lower == "color":
            parsed = parse_color_or_none(value)
            if parsed is None:
                # 认不出来的颜色（模型写了 oklch(...) 之类）→ 保留原色，别默默变成白色
                logging.warning("颜色值认不出来，已保留原来的颜色：%r", value)
            else:
                state["color"], state["colorAlpha"] = parsed
        elif lower in ("background", "background-color"):
            _apply_background(state, value)
        elif lower == "background-image":
            for layer in parse_gradients(value):
                state["bgLayers"].append(layer)
        elif lower in ("background-position", "background-size", "background-repeat",
                       "background-origin", "background-clip"):
            _apply_layer_property(state, lower, value)
        elif lower == "letter-spacing":
            state["letterSpacing"] = _clamp(_length(value, "px"), 0, 6)
        elif lower == "box-shadow":
            state["boxShadows"] = parse_box_shadows(value)
        elif lower == "text-shadow":
            state["textShadows"] = parse_text_shadows(value)
        elif lower == "opacity":
            state["opacity"] = _clamp(_number(value), 0.1, 1)
        elif lower == "box-sizing":
            state["boxSizing"] = value.strip().lower() != "content-box"
        elif lower == "border":
            _apply_border(state, value)
        elif lower in ("border-width", "border-style", "border-color"):
            _apply_border_longhand(state, lower, value)
        else:
            state["extras"].append(f"{prop}: {value}")
    _flush_layer_properties(state)
    return state


def _drop_extra(state: Dict[str, Any], prop: str) -> None:
    """把 extras 里同名的「手写声明」去掉（这个属性已经能用状态表示了，后写的覆盖先写的）。"""
    state["extras"] = [item for item in state.get("extras") or []
                       if str(item).split(":", 1)[0].strip() != prop]


def _apply_layer_property(state: Dict[str, Any], prop: str, value: str) -> None:
    """`background-position` / `-size` / `-repeat` / `-origin` / `-clip` → **最上层**的渐变层。

    这几个属性是「每个背景块各一份」的，而我们只有一组控件，所以按最常见的情况处理：
    写到 index 0（最上面那一层）。写的时候还没读到 `background` 就先挂在 `bgPending` 上，
    `apply_decls()` 收尾时再落到最上层（wikitext 里两种顺序都常见）。
    """
    pending = state.setdefault("bgPending", {})
    value = (value or "").strip()
    if prop == "background-position":
        position = parse_position(value)
        if position:
            pending.update({"posSet": True, "posX": position["x"],
                            "posUnitX": position["xu"], "posY": position["y"],
                            "posUnitY": position["yu"]})
    elif prop == "background-size":
        if re.fullmatch(r"cover|contain", value, re.I):
            pending.update({"sizeSet": True, "sizeW": 0.0, "sizeUnitW": value.lower(),
                            "sizeH": 0.0, "sizeUnitH": value.lower()})
        else:
            items = [parse_length(token) for token in value.split()]
            items = [item for item in items if item is not None]
            if items:
                first = items[0]
                second = items[1] if len(items) > 1 else {"value": 0.0, "unit": "auto"}
                pending.update({"sizeSet": True,
                                "sizeW": first["value"], "sizeUnitW": first["unit"],
                                "sizeH": second["value"], "sizeUnitH": second["unit"]})
    elif prop == "background-repeat":
        for keyword in REPEAT_KEYWORDS:
            if re.search(r"(?<![\w-])" + keyword + r"(?![\w-])", value, re.I):
                pending["repeatMode"] = keyword
                break
    elif prop == "background-origin":
        if value.lower() in BOX_KEYWORDS:
            pending["origin"] = value.lower()
    elif prop == "background-clip":
        if value.lower() in BOX_KEYWORDS + ("text",):
            pending["clip"] = value.lower()


def _flush_layer_properties(state: Dict[str, Any]) -> None:
    """把 `bgPending` 上攒的 `background-*` 属性落到最上层渐变层里。"""
    pending = state.pop("bgPending", None)
    layers = state.get("bgLayers") or []
    if not pending or not layers:
        return
    layers[0].update(pending)


def _apply_background(state: Dict[str, Any], value: str) -> None:
    text = (value or "").strip()
    if not text:
        return
    layers = parse_gradients(text)
    solids: List[str] = []
    for chunk in _split_top_level(text):
        if "gradient(" in chunk.lower():
            continue
        solids.append(chunk.strip())
    if layers:
        state["bgLayers"] = layers
    else:
        # 纯色背景：旧的渐变得让位 —— 否则它盖在新颜色上面，用户看到的还是老配色
        # （用户 2026-09-29 报：「每次生成 |containerstyle 的颜色都几乎一模一样」，
        #   实测模型回 `background-color: #e2e3e8`，输出却还是 `background: <旧深色渐变>, #e2e3e8`）。
        state["bgLayers"] = []
    if solids:
        last = solids[-1]
        parsed = parse_color_or_none(last)
        if parsed is None:
            logging.warning("底色认不出来，已保留原来的底色：%r", last)
        else:
            state["bgSolid"], state["bgSolidAlpha"] = parsed
        state["bgSet"] = True
    elif layers:
        state["bgSet"] = True


def _apply_border(state: Dict[str, Any], value: str) -> None:
    text = (value or "").strip()
    if text in ("0", "none"):
        state["borderWidth"] = 0
        state["borderStyle"] = "none"
        return
    # 颜色要**整块**先摘出来：`rgba(20, 18, 34, 0.6)` 按空白切会变成四个碎片
    rest, color = _pop_color(text)
    width = 0
    for chunk in re.split(r"\s+", rest.strip()):
        if not chunk:
            continue
        if re.match(r"^[\d.]+(px)?$", chunk):
            width = float(chunk.rstrip("px")) if chunk.rstrip("px") else 0
        elif chunk in BORDER_STYLES:
            state["borderStyle"] = chunk
    if color:
        if color.lower() == "currentcolor":
            state["borderCurrent"] = True
        else:
            parsed = parse_color_or_none(color)
            if parsed is None:
                logging.warning("边框颜色认不出来，已保留原来的边框色：%r", color)
            else:
                state["borderCurrent"] = False
                state["borderColor"], state["borderAlpha"] = parsed
    state["borderWidth"] = _clamp(width, 0, 10)


def _apply_border_longhand(state: Dict[str, Any], prop: str, value: str) -> None:
    if prop == "border-width":
        state["borderWidth"] = _clamp(_length(value, "px"), 0, 10)
    elif prop == "border-style":
        state["borderStyle"] = value.strip()
    else:
        if value.strip().lower() == "currentcolor":
            state["borderCurrent"] = True
        else:
            parsed = parse_color_or_none(value)
            if parsed is None:
                logging.warning("边框颜色认不出来，已保留原来的边框色：%r", value)
            else:
                state["borderCurrent"] = False
                state["borderColor"], state["borderAlpha"] = parsed


def _split_top_level(text: str) -> List[str]:
    """按顶层逗号切分（括号里的逗号不算）。"""
    parts, depth, current = [], 0, []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _split_function(text: str) -> Tuple[str, str]:
    """`linear-gradient(...) 0 0/20px 20px repeat` → `(函数文本, 尾部参数)`。

    括号里的逗号 / 括号要配对，所以不能拿正则一路啃：`rgba(0,0,0,.5)`、`calc(42.5% + 1px)`
    都在函数体里，只有**配平的**那个 `)` 才是函数的结尾。尾部参数（背景块的
    position / size / repeat / origin / clip）是 CSS 允许跟在 image 后面的东西，
    以前 `_GRADIENT_RE` 要求渐变占满整个背景块，于是这些写法整块都被丢掉。
    """
    text = (text or "").strip()
    start = text.find("(")
    if start < 0:
        return "", text
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[:index + 1], text[index + 1:].strip()
    return "", text                      # 括号没配平：认不出


_ANGLE_RE = re.compile(r"^(-?[\d.]+)(deg|rad|turn|grad)?$", re.IGNORECASE)
_LENGTH_RE = re.compile(
    r"^(-?[\d.]+)(px|%|em|rem|vw|vh|vmin|vmax|pt|pc|cm|mm|in|ch|ex|deg|rad|turn|grad)?$",
    re.IGNORECASE)
# `to <方向>`：CSS 允许两个关键字任意顺序（`to right top` == `to top right`）
_SIDE_TOKENS = {"top", "bottom", "left", "right"}


def parse_length(text: str) -> Optional[Dict[str, Any]]:
    """`25px` / `60%` / `0` / `-20px` / `auto` → `{"value": 25, "unit": "px"}`；认不出返回 None。

    没有单位的 `0` 按 CSS 算长度（`at 0 0`、`position: 0 0` 都很常见），给它 `px`。
    """
    text = str(text or "").strip().lower()
    if text == "auto":
        return {"value": 0.0, "unit": "auto"}
    match = _LENGTH_RE.match(text)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if not unit:
        if value != 0:
            return None                  # `25` 这种没单位的非零长度 CSS 本身也不合法
        unit = "px"
    return {"value": value, "unit": unit}


def parse_position(text: str) -> Optional[Dict[str, Any]]:
    """`left bottom` / `75px 25px` / `center 10px` / `0 100%` → `{x, xu, y, yu}`。

    CSS 的 `background-position` / `radial-gradient(at …)` / `conic-gradient(at …)` 是同一套语法：
    关键字（left / center / right / top / bottom）与长度可以混着写，只有一个值时另一个取 center。
    """
    tokens = [token for token in str(text or "").split() if token]
    if not tokens:
        return None
    result: Dict[str, Any] = {"x": None, "xu": "%", "y": None, "yu": "%", "set": False}
    keywords = [token for token in tokens if token.lower() in
                {"left", "center", "right", "top", "bottom"}]
    lengths = [token for token in tokens if token.lower() not in
               {"left", "center", "right", "top", "bottom"}]
    for token in keywords:
        low = token.lower()
        if low in ("left", "right") and result["x"] is None:
            result["x"], result["xu"] = (0.0 if low == "left" else 100.0), "%"
        elif low in ("top", "bottom") and result["y"] is None:
            result["y"], result["yu"] = (0.0 if low == "top" else 100.0), "%"
        elif low == "center":
            if result["x"] is None:
                result["x"], result["xu"] = 50.0, "%"
            if result["y"] is None:
                result["y"], result["yu"] = 50.0, "%"
    parsed = [parse_length(token) for token in lengths]
    parsed = [item for item in parsed if item is not None]
    if len(parsed) > 2 or (parsed and result["x"] is not None and result["y"] is not None
                           and not keywords):
        return None
    # 长度按顺序填进还空着的轴（`center 10px` → x 已有、10px 归 y）
    for item in parsed:
        if result["x"] is None:
            result["x"], result["xu"] = item["value"], item["unit"]
        elif result["y"] is None:
            result["y"], result["yu"] = item["value"], item["unit"]
        else:
            return None
    if result["x"] is None:
        result["x"], result["xu"] = 50.0, "%"
    if result["y"] is None:
        result["y"], result["yu"] = 50.0, "%"
    result["set"] = True
    return result


def _canonical_direction(text: str) -> str:
    """`to right top` / `right top` → `to top right`；不是方向关键字返回空串。"""
    tokens = [token.lower() for token in str(text or "").split()]
    if tokens and tokens[0] == "to":
        tokens = tokens[1:]
    if not tokens or len(tokens) > 2 or any(token not in _SIDE_TOKENS for token in tokens):
        return ""
    order = {"top": 0, "bottom": 1, "left": 2, "right": 3}
    return "to " + " ".join(sorted(set(tokens), key=lambda item: order[item]))


# 背景块的 `origin` / `clip` 关键字
BOX_KEYWORDS = ("padding-box", "border-box", "content-box")
# 背景块的重复方式
REPEAT_KEYWORDS = ("repeat", "repeat-x", "repeat-y", "no-repeat", "space", "round")


def _parse_layer_tail(tail: str) -> Dict[str, Any]:
    """渐变函数后面那段背景块参数 → 位置 / 尺寸 / 重复 / origin / clip。

    `10px 10px`、`0 0/20px 20px`、`0 0/auto 50px`、`padding-box border-box`、`text` 都在这里。
    """
    result: Dict[str, Any] = {"posSet": False, "sizeSet": False,
                              "repeatMode": "", "origin": "", "clip": ""}
    tail = (tail or "").strip()
    if not tail:
        return result
    # 关键字（repeat / 盒子 / text / 附件）先从**整段**里摘掉：它们既可能写在 `/` 前面，
    # 也可能跟在 size 后面（`0 0/20px 20px no-repeat` 是最常见的写法）
    for flag in ("fixed", "local", "scroll"):
        tail = re.sub(r"\b" + flag + r"\b", " ", tail, flags=re.I)
    for keyword in BOX_KEYWORDS + ("text",):
        while re.search(r"\b" + keyword + r"\b", tail, re.I):
            tail = re.sub(r"\b" + keyword + r"\b", " ", tail, count=1, flags=re.I)
            if keyword == "text":
                result["clip"] = "text"
            elif result["origin"]:
                result["clip"] = result["clip"] or keyword
            else:
                result["origin"] = keyword
    for keyword in REPEAT_KEYWORDS:
        match = re.search(r"(?<![\w-])" + keyword + r"(?![\w-])", tail, re.I)
        if match:
            result["repeatMode"] = match.group(0).lower()
            tail = tail[:match.start()] + " " + tail[match.end():]
            break
    position_part, _, size_part = tail.partition("/")
    position_part = position_part.strip()
    if position_part:
        position = parse_position(position_part)
        if position:
            result.update({"posSet": True, "posX": position["x"], "posUnitX": position["xu"],
                           "posY": position["y"], "posUnitY": position["yu"]})
    size_part = size_part.strip()
    if size_part and re.fullmatch(r"cover|contain", size_part, re.I):
        result["sizeSet"] = True
        result["sizeW"], result["sizeUnitW"] = 0.0, size_part.lower()
        result["sizeH"], result["sizeUnitH"] = 0.0, size_part.lower()
    elif size_part:
        items = [parse_length(token) for token in size_part.split()]
        items = [item for item in items if item is not None]
        if items:
            first = items[0]
            second = items[1] if len(items) > 1 else {"value": 0.0, "unit": "auto"}
            result.update({"sizeSet": True,
                           "sizeW": first["value"], "sizeUnitW": first["unit"],
                           "sizeH": second["value"], "sizeUnitH": second["unit"]})
    return result


_GRADIENT_HEAD_RE = re.compile(
    r"^(?P<repeating>repeating-)?(?P<kind>linear|radial|conic)-gradient$", re.IGNORECASE)

# 径向 / 锥形头部里可能出现的单词（有这些就说明这一段是头部而不是色标）
_HEAD_KEYWORDS = ("circle", "ellipse", "closest-side", "closest-corner",
                  "farthest-side", "farthest-corner", "at", "from")
# 颜色 token：出现颜色的片段一定是色标，不能再当头部吃掉
_COLOR_ISH_RE = re.compile(r"#|rgba?\(|hsla?\(|hsva?\(", re.IGNORECASE)


def _looks_like_head(chunk: str) -> bool:
    """这一段是不是径向 / 锥形渐变的头部（`circle closest-side at 75px 25px`）？

    必须带头部关键字、或者整段都是长度 —— `#FF7777 10deg` 里的 `10deg` 是**色标位置**，
    以前被当成半径吃掉了，于是第一个色标整条消失（用户 2026-10-30 报）。
    """
    text = str(chunk or "").strip()
    if not text or _COLOR_ISH_RE.search(text):
        return False
    tokens = text.replace(",", " ").split()
    if not tokens:
        return False
    if any(token.lower() in _HEAD_KEYWORDS for token in tokens):
        return True
    return all(parse_length(token) is not None for token in tokens)


def parse_gradients(text: str) -> List[Dict[str, Any]]:
    """从 `background` 值里认出各层渐变（认不出的整段丢掉，由 extras 兜底更稳）。

    每层除了渐变本身，还收下背景块的参数（`position` / `size` / `repeat` / `origin` / `clip`），
    这样 `linear-gradient(…) 0 0/20px 20px repeat` 这种写法不会整块丢掉（用户 2026-10-28 报）。
    """
    layers: List[Dict[str, Any]] = []
    text = re.sub(r"'{2,}", " ", str(text or ""))   # 页面上被写成粗体的 `'''calc(…)'''`
    for chunk in _split_top_level(text):
        function, tail = _split_function(chunk)
        if not function:
            continue
        head, _paren, body = function.partition("(")
        body = body[:-1] if body.endswith(")") else body       # 去掉函数自己的右括号
        match = _GRADIENT_HEAD_RE.match(head.strip())
        if not match:
            continue
        kind = match.group("kind").lower()
        layer = default_layer(kind)
        if kind == "conic":
            # CSS 的 `from` 默认是 0deg（`default_layer()` 给**新建层**的是 135deg）
            layer["angle"], layer["angleUnit"] = 0.0, "deg"
        elif kind == "radial":
            # CSS 的默认形状是椭圆（`default_layer()` 给新建层的是圆形）
            layer["shape"] = "ellipse"
            layer["headSet"] = False
        layer["repeat"] = bool(match.group("repeating"))
        layer.update(_parse_layer_tail(tail))
        stop_text = body
        if kind == "linear":
            first, _comma, rest = body.partition(",")
            direction = _canonical_direction(first)
            angle = _ANGLE_RE.match(first.strip())
            if direction:
                layer["dir"] = direction
                layer["angle"] = DIRECTION_ANGLES.get(direction, 180.0)
                stop_text = rest
            elif angle:
                layer["angle"] = float(angle.group(1))
                layer["angleUnit"] = (angle.group(2) or "deg").lower()
                stop_text = rest
            else:
                layer["angle"], layer["angleUnit"] = 180.0, "deg"
        else:
            stop_text = _parse_radial_conic_head(layer, kind, body)
        stops = _parse_stops(stop_text, kind)
        if len(stops) < 2:
            continue
        layer["stops"] = stops
        layers.append(layer)
    return layers


# 方向关键字 → 角度（**方形**盒子下的值）。角方向在真正的盒子里要按宽高重算，
# 见 `style_preview.css_angle()`（200×100 的 `to top right` 是 26.57°，不是 45°）。
DIRECTION_ANGLES = {"to top": 0.0, "to right": 90.0, "to bottom": 180.0, "to left": 270.0,
                    "to top right": 45.0, "to bottom right": 135.0,
                    "to bottom left": 225.0, "to top left": 315.0}


def _parse_radial_conic_head(layer: Dict[str, Any], kind: str, body: str) -> str:
    """径向 / 锥形的头部（`circle closest-side at 75px 25px`）→ 写回 layer，返回色标文本。

    头部与色标之间用逗号分隔，但头部里的 `at …` 也可能带逗号之外的东西，所以按「头部到底有几个
    逗号前的片段」来判断：逐个片段尝试当头部解析，直到某个片段不再像头部。
    """
    chunks = _split_top_level(body)
    head_end = 0
    if chunks and _looks_like_head(chunks[0]):
        head_end = 1
    if head_end == 0:
        # 没有头部：`radial-gradient(#FF7777,#77FF77,#7777FF)` 直接从色标开始
        layer["cx"], layer["cy"], layer["cxUnit"] = 50.0, 50.0, "%"
        layer["cyUnit"] = "%"
        return body
    layer["headSet"] = True
    size_set = False
    head = chunks[0]
    rest = ",".join(chunks[1:])
    # `at <位置>`（位置可能带空格，所以取 `at` 之后的全部）
    match = re.search(r"\bat\b(.*)$", head, re.I)
    if match:
        position = parse_position(match.group(1))
        if position:
            layer.update({"cx": position["x"], "cxUnit": position["xu"],
                          "cy": position["y"], "cyUnit": position["yu"]})
        head = head[:match.start()]
    match = re.search(r"\bfrom\s+(-?[\d.]+)(deg|rad|turn|grad)\b", head, re.I)
    if match:
        # 锥形的 `from` 角度与线性的「角度」共用一个字段（面板上也是同一个「角度」微调框）
        layer["angle"] = float(match.group(1))
        layer["angleUnit"] = match.group(2).lower()
        head = head[:match.start()] + head[match.end():]
    shape = re.search(r"\b(circle|ellipse)\b", head, re.I)
    if shape:
        layer["shape"] = shape.group(1).lower()
        head = head[:shape.start()] + head[shape.end():]
    keyword = re.search(r"\b(closest-side|closest-corner|farthest-side|farthest-corner)\b",
                        head, re.I)
    if keyword:
        layer["sizeKw"] = keyword.group(1).lower()
        size_set = True
        head = head[:keyword.start()] + head[keyword.end():]
    lengths = [parse_length(token) for token in head.split()]
    lengths = [item for item in lengths if item is not None]
    if lengths:
        size_set = True
        first = lengths[0]
        layer["radiusX"], layer["radiusUnitX"] = first["value"], first["unit"]
        if len(lengths) > 1:
            layer["radiusY"], layer["radiusUnitY"] = lengths[1]["value"], lengths[1]["unit"]
        elif first["unit"] != "%":
            # 一个长度且不是百分比 → 圆形半径（CSS：单长度只能是圆）
            layer["shape"] = "circle"
            layer["radiusY"], layer["radiusUnitY"] = first["value"], first["unit"]
    # 只写了 `at …` / `from …` 不算给了尺寸：写回时不能再凭空补一个 `60% 40%`
    layer["headSet"] = size_set
    return rest


def _parse_stops(text: str, kind: str = "linear") -> List[Dict[str, Any]]:
    """色标文本 → `[{color, alpha, pos, unit, aa, hint}]`。

    与以前的区别（都对照 [[User:JackBlock/渐变色使用]] 校正，用户 2026-10-28 报）：

    * **位置带单位**：`25px` 不再被当成 `25%`（`unit` 记下来，画的时候按盒子尺寸换算）；
    * **不写位置时等距分布**：`linear-gradient(#FF7777,#77FF77,#7777FF)` 以前排成 0/100/100，
      现在是 0/50/100；
    * **颜色提示**（`#FF7777,20%,#7777FF`）：只有位置没有颜色 → 记成 `hint`，不再变成一个白色色标；
    * **一个节点两个位置**（`#77FF77 20% 80%`）→ 拆成两个同色色标（CSS 里就是一条色带）；
    * 锥形渐变的 `10deg` / `2rad` 色标也认。
    """
    stops: List[Dict[str, Any]] = []
    angle_units = ("deg", "rad", "turn", "grad", "%") if kind == "conic" else ("px", "%")
    for chunk in _split_top_level(text):
        chunk = chunk.strip()
        if not chunk:
            continue
        body = chunk
        positions: List[Dict[str, Any]] = []
        while True:
            # `(?<![\w.#-])`：`#000` / `#000000` 末尾的 0 也是数字，不能被当成位置吃掉
            match = re.search(r"(?<![\w.#-])(-?[\d.]+)(deg|rad|turn|grad|px|%)?\s*$", body,
                              re.IGNORECASE)
            if not match:
                break
            unit = (match.group(2) or "").lower()
            if not unit:
                if float(match.group(1)) != 0:
                    break                       # 不带单位的非零值不是位置
                unit = "px"
            if unit not in angle_units:
                break
            positions.insert(0, {"value": float(match.group(1)), "unit": unit})
            body = body[:match.start()].strip()
            if len(positions) >= 2:
                break
        antialias = "calc(" in body.lower()
        if antialias:                           # calc(33.3% - 1px) 是我们自己写出去的抗锯齿写法
            inner = re.search(r"calc\(([^)]*)\)", body, re.I)
            body = re.sub(r"\s*calc\([^)]*\)", "", body, flags=re.I).strip()
            if inner and not positions:
                found = re.search(r"(-?[\d.]+)(%|px)?", inner.group(1))
                if found:
                    positions.append({"value": float(found.group(1)),
                                      "unit": (found.group(2) or "%").lower()})
        if body:
            color, alpha = parse_color(body)
            entries = positions or [None]
            for index, position in enumerate(entries):
                stops.append({"color": color, "alpha": alpha, "aa": antialias,
                              "hint": False,
                              "pos": position["value"] if position else None,
                              "unit": position["unit"] if position else None})
        else:
            # 只有位置没有颜色 → 颜色提示（两个色标中间色的位置）
            position = positions[0] if positions else None
            stops.append({"color": None, "alpha": 1.0, "aa": False, "hint": True,
                          "pos": position["value"] if position else None,
                          "unit": position["unit"] if position else None})
    if kind == "conic":
        _into_degrees(stops)                    # 锥形的 `%` / `rad` 先统一成 `deg`，才好排位置
    _fill_missing_positions(stops, kind)
    if len(stops) > MAX_STOPS:
        stops = stops[:MAX_STOPS]
    # 抗锯齿写法会成对出现（同一位置两个色标），合并成一个带 aa 标记的
    merged: List[Dict[str, Any]] = []
    for stop in stops:
        previous = merged[-1] if merged else None
        if (previous is not None and not stop["hint"] and not previous.get("hint")
                and stop["color"] == previous["color"]
                and abs(_num(stop["pos"]) - _num(previous["pos"])) < 0.2
                and stop.get("unit") == previous.get("unit")):
            previous["aa"] = True
            continue
        merged.append(stop)
    for stop in merged:                         # 单位没写的一律按 %（老状态 / 手写的都走这条）
        if not stop.get("unit"):
            stop["unit"] = "%"
    return merged


_ANGLE_SCALE = {"deg": 1.0, "rad": 180.0 / math.pi, "turn": 360.0, "grad": 0.9}


def _into_degrees(stops: List[Dict[str, Any]]) -> None:
    """锥形渐变的色标位置统一成 `deg`（`100%` = 360deg、`1turn` = 360deg）。"""
    for stop in stops:
        if stop.get("pos") is None:
            stop["unit"] = "deg"
            continue
        unit = str(stop.get("unit") or "%").lower()
        value = _num(stop.get("pos"))
        if unit == "%":
            value *= 3.6
        elif unit in _ANGLE_SCALE:
            value *= _ANGLE_SCALE[unit]
        stop["pos"], stop["unit"] = value, "deg"


def _fill_missing_positions(stops: List[Dict[str, Any]], kind: str) -> None:
    """按 CSS 规则补上没有位置（或位置比前一个还小）的色标。

    CSS Images 3：第一个色标没写位置 → 0；最后一个没写 → 100%（锥形是 360deg）；
    中间的没写 → 在前后两个**已定**位置之间等距分布；位置比前一个小的按前一个算。
    """
    if not stops:
        return
    end_default = 360.0 if kind == "conic" else 100.0
    base_unit = "deg" if kind == "conic" else "%"
    if stops[0]["pos"] is None:
        stops[0]["pos"], stops[0]["unit"] = 0.0, (stops[0]["unit"] or base_unit)
    if stops[-1]["pos"] is None:
        stops[-1]["pos"] = end_default
        stops[-1]["unit"] = stops[-1]["unit"] or base_unit
    index = 1
    while index < len(stops) - 1:
        if stops[index]["pos"] is not None:
            index += 1
            continue
        end = index
        while end < len(stops) and stops[end]["pos"] is None:
            end += 1
        start_pos = _num(stops[index - 1]["pos"])
        end_pos = _num(stops[end]["pos"])
        step = (end_pos - start_pos) / (end - index + 1)
        for offset in range(index, end):
            stops[offset]["pos"] = start_pos + step * (offset - index + 1)
            stops[offset]["unit"] = (stops[offset]["unit"] or stops[index - 1]["unit"]
                                     or base_unit)
        index = end
    for stop in stops:                          # 位置必须单调不减
        stop["pos"] = _num(stop.get("pos"))
    for index in range(1, len(stops)):
        if stops[index]["pos"] < stops[index - 1]["pos"]:
            stops[index]["pos"] = stops[index - 1]["pos"]



# 颜色 token（`rgba(20, 18, 34, 0.6)` 里有空格，不能用空白切分）
_COLOR_TOKEN_RE = re.compile(
    r"rgba?\([^)]*\)|#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})"
    r"|\b(?:black|white|red|green|blue|currentcolor|transparent)\b", re.IGNORECASE)


def _pop_color(text: str) -> Tuple[str, str]:
    """把一段 CSS 值里的颜色**整块**摘出来 → (剩下的文字, 颜色文本)。"""
    match = _COLOR_TOKEN_RE.search(str(text or ""))
    if match is None:
        return str(text or ""), ""
    rest = str(text)[:match.start()] + " " + str(text)[match.end():]
    return rest, match.group(0)


def _shadow_parts(chunk: str) -> Tuple[List[float], str, bool]:
    """阴影的一段 → (长度列表, 颜色文本, 是否 inset)；颜色带空格（rgba）时也不会被切碎。"""
    raw = str(chunk or "")
    inset = bool(re.search(r"(?:^|\s)inset(?:\s|$)", raw, re.IGNORECASE))
    text = re.sub(r"(?:^|\s)inset(?:\s|$)", " ", raw, flags=re.IGNORECASE)
    text, color = _pop_color(text)
    numbers = [float(piece.rstrip("px")) for piece in re.split(r"\s+", text.strip())
               if re.match(r"^-?[\d.]+(px)?$", piece)]
    return numbers, color, inset


def parse_box_shadows(text: str) -> List[Dict[str, Any]]:
    shadows = []
    for chunk in _split_top_level(text):
        numbers, color, inset = _shadow_parts(chunk)
        item = {"x": 0, "y": 0, "blur": 0, "spread": 0, "color": "#000000", "alpha": 1.0,
                "inset": inset}
        if color:
            item["color"], item["alpha"] = parse_color(color)
        for index, key in enumerate(("x", "y", "blur", "spread")):
            if index < len(numbers):
                item[key] = numbers[index]
        shadows.append(item)
    return shadows[:MAX_BOX_SHADOWS]


def parse_text_shadows(text: str) -> List[Dict[str, Any]]:
    shadows = []
    for chunk in _split_top_level(text):
        numbers, color, _inset = _shadow_parts(chunk)
        item = {"x": 0, "y": 0, "blur": 0, "color": "#000000", "alpha": 1.0}
        if color:
            item["color"], item["alpha"] = parse_color(color)
        for index, key in enumerate(("x", "y", "blur")):
            if index < len(numbers):
                item[key] = numbers[index]
        shadows.append(item)
    return shadows[:MAX_TEXT_SHADOWS]


def _length(value: Any, unit: str) -> float:
    match = re.search(r"(-?[\d.]+)", str(value or ""))
    return float(match.group(1)) if match else 0.0


def _number(value: Any) -> float:
    match = re.search(r"(-?[\d.]+)", str(value or ""))
    return float(match.group(1)) if match else 0.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _weight(value: Any) -> int:
    text = str(value or "").strip().lower()
    if text == "bold":
        return 700
    if text == "normal":
        return 400
    number = _number(text)
    return int(_clamp(number or 400, 300, 900))


def parse_wiki_text(text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """把编辑器产出的参数文本解析回状态（打开已有条目时用）。"""
    params = split_params(text)
    states = [blank_state() for _ in range(3)]
    tpl_states = tpl_default_states()
    for index in range(3):
        raw = params.get(f"颜色{index + 1}")
        if raw is None:
            continue
        decls = parse_decl_text(raw)
        apply_decls(states[index], decls)
        states[index]["bgSet"] = any(prop in ("background", "background-color") for prop, _ in decls)
    intro = tpl_states["introLabel"]
    if "lbgcolor" in params:
        # lbgcolor 是「裸底色 + 盒子相关的额外声明」：第一段（没有属性名）进底色
        intro["extras"] = []
        decls = parse_decl_text(params["lbgcolor"])
        apply_decls(intro, decls)
        intro["force"] |= {prop for prop, _value in decls}
        intro["bgSet"] = True
    if "ltcolor" in params:
        # ltcolor 是「文字色 + 文字相关的额外声明」（AI 会把 font-size / font-weight 写在这行）
        head, _sep, tail = params["ltcolor"].strip().partition(";")
        if ":" in head:
            # 整行都带属性名，交给 apply_decls（color 也在里面）
            decls = parse_decl_text(params["ltcolor"])
        else:
            intro["color"], intro["colorAlpha"] = parse_color(head)
            decls = parse_decl_text(tail)
        apply_decls(intro, decls)
        intro["force"] |= {prop for prop, _value in decls}
    if "rbdcolor" in params:
        # 原文里已经有列表格边框色 → 记下来写回去（我们不再自动补这一行）。
        # 它正好等于底色时认为「只是把默认值写实了」→ 以后跟着底色同步；
        # 用户特意设了别的颜色就原样保留。
        raw = params["rbdcolor"].strip()
        intro["rbdcolor"] = raw
        parsed = parse_color_or_none(raw)
        intro["rbdFollowsBg"] = bool(parsed) and css_color(*parsed) == css_color(
            intro.get("bgSolid"), intro.get("bgSolidAlpha", 1.0))
    for key, spec in TPL_TARGETS.items():
        param = spec["param"]
        if key == "introLabel" or param not in params:
            continue
        state = tpl_states[key]
        state["enabled"] = True
        raw = params[param]
        if key == "lyrContainer":
            state["extras"] = []
        decls = parse_decl_text(raw)
        apply_decls(state, decls)
        state["force"] |= {prop for prop, _value in decls}
        state["bgSet"] = any(prop in ("background", "background-color") for prop, _ in decls)
    return states, tpl_states


def parse_code_css(text: str) -> List[Tuple[str, str]]:
    """代码框里的 `selector { … }` → 声明列表（认不出的属性留给 extras）。"""
    body = text or ""
    match = re.search(r"\{([\s\S]*)\}", body)
    if match:
        body = match.group(1)
    body = re.sub(r"/\*[\s\S]*?\*/", "", body)
    return parse_decl_text(body)


def target_label(key: str) -> str:
    """对象 id / 参数名 → 中文名（提示里报「模型没返回『标签格』」用）。

    `songboxGlobal` / `pill0` 这些不是模板目标，退回原名。
    """
    spec = TPL_TARGETS.get(key)
    if spec:
        return str(spec["label"])
    for item_key, item_spec in TPL_TARGETS.items():
        if item_spec["param"] == key:
            return str(item_spec["label"])
    return {"songboxGlobal": "全局"}.get(key, key)


def ai_target_id(state: Dict[str, Any], index: int) -> str:
    """AI 面板里「当前对象」的 id（与 html 版的 pill{index} / songboxGlobal 一致）。"""
    return "songboxGlobal" if index < 0 else f"pill{index}"


def template_target(key: str, state: Dict[str, Any], color_only: bool) -> dict:
    """一个模板对象（Introduction 标签格 / 歌词各栏）的 AI 目标描述。"""
    allow = list(TPL_ALLOW[key])
    props = [prop for prop in allow if prop in AI_COLOR_PROPS] if color_only else allow
    return {"id": key, "label": TPL_TARGETS[key]["label"], "props": props,
            "current": css_text(delta_decls(state), " ")}


def ai_payload_targets(states: Sequence[Dict[str, Any]], index: Any,
                       tpl_states: Dict[str, Dict[str, Any]], color_only: bool) -> List[dict]:
    """一次 AI 生成要覆盖的对象列表（含当前样式文本与可用属性）。

    `index` 是界面上的「当前对象」：Songbox 是 0/1/2（-1 = 全局），
    Introduction / 歌词 那几项是**模板状态名**（`introLabel` / `lyrContainer` / `lyrOrig` / `lyrTrans`），
    所以字符串要把对象当模板处理 —— 以前这里直接和 0 比大小，切到「Introduction」再点生成
    就 `TypeError: '>=' not supported between instances of 'str' and 'int'`（界面报错）。
    """
    if isinstance(index, str):
        if index not in tpl_states or index not in TPL_ALLOW:
            logging.warning("AI：认不出当前对象 %r，跳过", index)
            return []
        return [template_target(index, tpl_states[index], color_only)]
    props = list(AI_COLOR_PROPS if color_only else AI_SONGBOX_PROPS)
    if index >= 0:
        state = states[index]
        return [{"id": ai_target_id(state, index), "label": RECT_LABELS[index],
                 "props": props, "current": css_text(build_decls(state), " ")}]
    return [{"id": "songboxGlobal", "label": "全局", "props": props,
             "current": css_text(build_decls(states[0]), " ")}]


def ai_all_targets(states: Sequence[Dict[str, Any]],
                   tpl_states: Dict[str, Dict[str, Any]], color_only: bool) -> List[dict]:
    """「全部（Songbox + Introduction + 歌词）」一次生成覆盖的对象。"""
    targets = ai_payload_targets(states, -1, tpl_states, color_only)
    for key in ("introLabel", "lyrContainer", "lyrOrig", "lyrTrans"):
        targets.append(template_target(key, tpl_states[key], color_only))
    return targets


def apply_ai_css(target: str, css: str, states: List[Dict[str, Any]],
                 tpl_states: Dict[str, Dict[str, Any]],
                 allowed: Optional[Sequence[str]] = None) -> None:
    """把 AI 返回的声明写进对应对象；AI 写过的属性记进 force，等于默认值也不会被丢掉。

    `allowed` 是**本次请求过的对象 id**（不给则不限）：只应用列表里的对象 —— 这样
    「只想改 Introduction」时绝不会连带把歌词的 `|rstyle` 写出来（用户 2026-09-29 报
    「生成 Introduction 的颜色时多了 rstyle」）。
    """
    if allowed is not None and target not in allowed:
        logging.warning("AI 返回了没请求的对象 %r，已忽略", target)
        return
    if target == "songboxGlobal":
        # 全局：三个颜色块一起改（「全局」态由界面自己同步）
        targets = list(states)
    elif target.startswith("pill"):
        index = int(target[len("pill"):])
        if not 0 <= index < len(states):
            return
        state = states[index]
        targets = [states[index]]
    elif target in tpl_states:
        state = tpl_states[target]
        targets = [tpl_states[target]]
        state["enabled"] = True
    else:
        return
    decls = parse_decl_text(css)
    for item in targets:
        apply_decls(item, decls)
    for prop, _value in decls:
        for item in targets:
            item.setdefault("force", set()).add(prop)
