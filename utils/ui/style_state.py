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
    """四个模板目标的默认样式（对应 JS 的 tplDefaultState）。"""
    states: Dict[str, Dict[str, Any]] = {}
    for key, spec in TPL_TARGETS.items():
        state = blank_state()
        state["bgSet"] = False
        # 只有 containerstyle / lstyle / rstyle 有「输出」开关；lbgcolor / ltcolor 恒输出
        state["enabled"] = not spec["toggle"]
        if key == "introLabel":
            state.update(bgSolid="#000000", color="#ffffff", bgSet=False, enabled=True)
        elif key == "lyrContainer":
            state.update(bgSolid="#ffffff", color="#000000", enabled=False)
        else:
            state.update(color="#000000", enabled=False)
        state["text"] = spec["label"]
        states[key] = state
    return states


def default_layer(kind: str = "linear") -> Dict[str, Any]:
    """新增一层渐变时的默认值（青绿系，与模板默认底色同一色系）。"""
    return {"kind": kind, "repeat": False, "hard": False, "angle": 135,
            "cx": 50, "cy": 50, "shape": "circle", "sizeUnit": "percent",
            "sx": 60, "sy": 40, "sizeKw": "",
            "stops": [{"color": DEFAULT_BG, "alpha": 1.0, "pos": 0, "aa": False},
                      {"color": "#6fe3da", "alpha": 1.0, "pos": 100, "aa": False}]}


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
    """一个色标（含「硬边 / 抗锯齿」）在给定位置上的文本。"""
    color = css_color(stop.get("color"), stop.get("alpha", 1.0))
    if stop.get("aa"):
        return [f"{color} calc({_trim(pos)}% - 1px)", f"{color} calc({_trim(pos)}% + 1px)"]
    return [f"{color} {_trim(pos)}%"]


def gradient_css(layer: Dict[str, Any]) -> str:
    """一层渐变 → `[repeating-]linear-gradient(...)`。"""
    stops: List[Dict[str, Any]] = list(layer.get("stops") or [])
    if len(stops) < 2:
        return ""
    parts: List[str] = []
    for index, stop in enumerate(stops):
        pos = _num(stop.get("pos"))
        if index == 0:
            parts.extend(_stop_css(stop, pos))
        elif layer.get("hard"):
            parts.extend(_stop_css(stops[index - 1], pos))
            parts.extend(_stop_css(stop, pos))
        else:
            parts.extend(_stop_css(stop, pos))
    prefix = "repeating-" if layer.get("repeat") else ""
    kind = layer.get("kind") or "linear"
    if kind == "radial":
        unit = "%" if (layer.get("sizeUnit") or "percent") == "percent" else "px"
        cx, cy = _trim(_num(layer.get("cx"), 50)), _trim(_num(layer.get("cy"), 50))
        size_kw = str(layer.get("sizeKw") or "").strip()
        shape = layer.get("shape") or "circle"
        if size_kw:
            head = f"{shape} {size_kw} at {cx}{unit} {cy}{unit}"
        elif shape == "ellipse":
            head = (f"ellipse {_trim(_num(layer.get('sx'), 60))}% "
                    f"{_trim(_num(layer.get('sy'), 40))}% at {cx}{unit} {cy}{unit}")
        else:
            head = f"circle at {cx}{unit} {cy}{unit}"
        return f"{prefix}radial-gradient({head}, {', '.join(parts)})"
    if kind == "conic":
        angle = _trim(_num(layer.get("angle")))
        return (f"{prefix}conic-gradient(from {angle}deg at {_trim(_num(layer.get('cx'), 50))}% "
                f"{_trim(_num(layer.get('cy'), 50))}%, {', '.join(parts)})")
    angle = _trim(_num(layer.get("angle")))
    return f"{prefix}linear-gradient({angle}deg, {', '.join(parts)})"


def background_value(state: Dict[str, Any], bold_first: bool = True) -> str:
    """`background` 的值：渐变层（index 0 最上）在上，纯底色垫底。"""
    layers = [gradient_css(layer) for layer in state.get("bgLayers") or []]
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


def tpl_wiki_text(states: Dict[str, Dict[str, Any]]) -> str:
    """`|lbgcolor` / `|ltcolor` / `|rbdcolor` / 歌词三项（顺序固定）。

    ⚠️ 标签格这里以前只写「底色 + 「额外声明」框里的东西」与文字色：模型里已经有的
    padding / border-radius / border / font-size / font-weight / box-shadow 全被丢掉，
    于是 AI 生成的那一大串 CSS 里只有两个颜色进得了 wikitext（用户 2026-09 报
    「AI 生成 Introduction 的颜色毫无变化」就是这个）。现在按盒子 / 文字分两拨一起写出去。
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
    value = "; ".join([css_color(intro.get("bgSolid"), intro.get("bgSolidAlpha", 1.0)), *box])
    lines.append(f"|lbgcolor = {value}")
    text = [f"{prop}: {value}" for prop, value
            in intro_decls(intro, INTRO_TEXT_PROPS, default)]
    lines.append("|ltcolor = " + "; ".join(
        [css_color(intro.get("color"), intro.get("colorAlpha", 1.0)), *text]))
    if box:
        # 标签格带额外声明时模板里的 border: <lbgcolor> 会被写坏，补上真正的边框色
        lines.append(f"|rbdcolor = {css_color(intro.get('bgSolid'), intro.get('bgSolidAlpha', 1.0))}")
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


# 编辑器写出去 / 认得回来的参数名（`|rbdcolor` 是我们自己算出来补上的，不算用户的输入）
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
    return state


def _drop_extra(state: Dict[str, Any], prop: str) -> None:
    """把 extras 里同名的「手写声明」去掉（这个属性已经能用状态表示了，后写的覆盖先写的）。"""
    state["extras"] = [item for item in state.get("extras") or []
                       if str(item).split(":", 1)[0].strip() != prop]


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


_GRADIENT_RE = re.compile(
    r"(?P<repeating>repeating-)?(?P<kind>linear|radial|conic)-gradient\((?P<body>.*)\)\s*$",
    re.IGNORECASE)


def parse_gradients(text: str) -> List[Dict[str, Any]]:
    """从 `background` 值里认出各层渐变（认不出的整段丢掉，由 extras 兜底更稳）。"""
    layers: List[Dict[str, Any]] = []
    for chunk in _split_top_level(text):
        match = _GRADIENT_RE.match(chunk.strip())
        if not match:
            continue
        body = match.group("body")
        kind = match.group("kind").lower()
        head, stop_text = "", ""
        layer = default_layer(kind)
        layer["repeat"] = bool(match.group("repeating"))
        if kind == "linear":
            # linear-gradient(135deg, ...) / linear-gradient(to right, ...)
            head, _, stop_text = body.partition(",")
            angle = re.search(r"(-?[\d.]+)deg", head)
            if angle:
                layer["angle"] = float(angle.group(1))
            elif "to " in head:
                layer["angle"] = {"to right": 90, "to left": 270, "to bottom": 180,
                                  "to top": 0}.get(head.strip().lower(), 135)
        elif kind == "radial":
            head, _, stop_text = body.partition(",")
            positions = re.search(r"at\s+(-?[\d.]+)%\s+(-?[\d.]+)%", head)
            if positions:
                layer["cx"] = float(positions.group(1))
                layer["cy"] = float(positions.group(2))
            shape = re.search(r"(circle|ellipse)", head)
            if shape:
                layer["shape"] = shape.group(1)
            size = re.search(r"(closest-side|farthest-side|closest-corner|farthest-corner)", head)
            if size:
                layer["sizeKw"] = size.group(1)
            else:
                radii = re.search(r"ellipse\s+(-?[\d.]+)%\s+(-?[\d.]+)%", head)
                if radii:
                    layer["sx"], layer["sy"] = float(radii.group(1)), float(radii.group(2))
        else:
            head, _, stop_text = body.partition(",")
            angle = re.search(r"(-?[\d.]+)deg", head)
            if angle:
                layer["angle"] = float(angle.group(1))
            positions = re.search(r"at\s+(-?[\d.]+)%\s+(-?[\d.]+)%", head)
            if positions:
                layer["cx"] = float(positions.group(1))
                layer["cy"] = float(positions.group(2))
        stops = _parse_stops(stop_text)
        if len(stops) < 2:
            continue
        layer["stops"] = stops
        layers.append(layer)
    return layers


def _parse_stops(text: str) -> List[Dict[str, Any]]:
    stops: List[Dict[str, Any]] = []
    for chunk in _split_top_level(text):
        chunk = chunk.strip()
        if not chunk:
            continue
        color_text = chunk
        position = None
        match = re.search(r"(-?[\d.]+)%\s*$", chunk)
        if match:
            position = float(match.group(1))
            color_text = chunk[:match.start()].strip()
        elif re.search(r"(-?[\d.]+)px\s*$", chunk):
            number = re.search(r"(-?[\d.]+)px\s*$", chunk)
            position = float(number.group(1))
            color_text = chunk[:number.start()].strip()
        antialias = "calc(" in color_text.lower()
        if antialias:                                   # calc(33.3% - 1px) 是我们自己写出去的抗锯齿写法
            color_text = re.sub(r"\s*calc\([^)]*\)", "", color_text).strip()
            match = re.search(r"(-?[\d.]+)%", chunk[chunk.lower().index("calc("):])
            if match:
                position = float(match.group(1))
        if position is None:
            position = 0.0 if not stops else 100.0
        color, alpha = parse_color(color_text)
        stops.append({"color": color, "alpha": alpha, "pos": position, "aa": antialias})
    if len(stops) > MAX_STOPS:
        stops = stops[:MAX_STOPS]
    # 抗锯齿写法会成对出现（同一位置两个色标），合并成一个带 aa 标记的
    merged: List[Dict[str, Any]] = []
    for stop in stops:
        previous = merged[-1] if merged else None
        if (previous is not None and stop["color"] == previous["color"]
                and abs(stop["pos"] - previous["pos"]) < 0.2):
            previous["aa"] = True
            continue
        merged.append(stop)
    return merged


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
