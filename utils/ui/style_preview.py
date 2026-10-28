"""「样式」页左边的实时预览：三个颜色块 + Introduction + 歌词。

只做视觉近似（Qt 画不出 CSS 的 box-shadow / 复杂渐变），够用来对着封面调色即可；
真正的输出永远是 utils.ui.style_state 生成的 wikitext 文本。画的内容按站上的真实
结构来：Songbox 三块竖排居中、Introduction 标签格 + 列表、歌词一行是「原文 + 译文并排」。
"""
from typing import Any, Dict, List, Optional, Sequence, Tuple

import math

from PyQt5 import QtCore, QtGui, QtWidgets

from utils.ui import style_state

# 预览里用的示例文字（与原 html 版预览块一致）
INTRO_ROWS = (("演唱", "初音未来"), ("P主", "P主"))
# `{{LyricsKai}}` 的每一行（`.Lyrics-line`）都是**原文 + 译文并排**（各占 49.85%），
# 所以示例也按「一行 = 原文 / 译文一对」来给；行数 = 元组长度。
LYRIC_ROWS = (("原文歌词示例文字", "中文译文示例文字"),
              ("原文歌词示例文字", "中文译文示例文字"))
LYRIC_LINE_HEIGHT = 32      # 单个 `.Lyrics-line` 的高度（原文 / 译文各占一半宽）
LYRIC_HALF = 0.4985         # `body.main-sm .Lyrics-has-translate` 里两栏各占的宽度比
LYRICS_WIDTH = 380          # 预览里的近似宽度（站上 `.Lyrics` 是块级、铺满正文宽）
CANVAS_PAD = 10             # paintEvent 里四周留的白（尺寸计算要加上）
PILL_GAP = 8                # 三块之间的间距（与画的时候一致）


def _qt_color(hex_value: Any, alpha: float = 1.0) -> QtGui.QColor:
    color = QtGui.QColor(style_state.norm_hex(hex_value) or "#ffffff")
    color.setAlphaF(max(0.0, min(1.0, float(alpha))))
    return color


def _qt_font_weight(css_weight: Any) -> int:
    """Map CSS 100–900 weights to Qt 5's 0–99 QFont weight scale."""
    weights = ((100, QtGui.QFont.Thin), (200, QtGui.QFont.ExtraLight),
               (300, QtGui.QFont.Light), (400, QtGui.QFont.Normal),
               (500, QtGui.QFont.Medium), (600, QtGui.QFont.DemiBold),
               (700, QtGui.QFont.Bold), (800, QtGui.QFont.ExtraBold),
               (900, QtGui.QFont.Black))
    value = int(float(css_weight or 400))
    return int(min(weights, key=lambda item: abs(item[0] - value))[1])


def _centered_rect(area: QtCore.QRect, top: int, width: int, height: int) -> QtCore.QRect:
    width = min(width, area.width())
    return QtCore.QRect(area.left() + (area.width() - width) // 2, top, width, height)


BORDER_STYLES = {
    "solid": QtCore.Qt.SolidLine, "dashed": QtCore.Qt.DashLine,
    "dotted": QtCore.Qt.DotLine, "double": QtCore.Qt.SolidLine,
}


def css_angle(layer: Dict[str, Any], width: float, height: float) -> float:
    """渐变角度（CSS 的 deg，0 = 向上、顺时针）。

    CSS 里四个**角**方向的角度取决于盒子宽高：渐变线要垂直穿过盒子中心并指向那个角，
    所以方形盒子的 `to top right` 是 45°、而 200×100 的盒子只有 26.57°
    （实测浏览器也一样）。以前一律按 135° 画，三个角方向全错位。
    """
    direction = str(layer.get("dir") or "")
    if direction:
        # CSS：角方向的渐变线垂直于「另外两个角」的连线。200×100 的盒子里
        # `to top right` 是 26.57°（方形盒子才正好是 45°）。
        offset = math.degrees(math.atan2(max(1.0, height), max(1.0, width)))
        return {"to top": 0.0, "to right": 90.0, "to bottom": 180.0, "to left": 270.0,
                "to top right": offset, "to bottom right": 180.0 - offset,
                "to bottom left": 180.0 + offset, "to top left": 360.0 - offset}.get(
                    direction, 180.0)
    angle = float(layer.get("angle") or 0)
    unit = str(layer.get("angleUnit") or "deg").lower()
    if unit == "rad":
        return math.degrees(angle)
    if unit == "turn":
        return angle * 360.0
    if unit == "grad":
        return angle * 0.9
    return angle


def _stop_fraction(stop: Dict[str, Any], length: float, kind: str) -> float:
    """色标位置 → 0~1 的比例（`px` 按渐变线长度换算，锥形按 360° 换算角度）。"""
    value = float(stop.get("pos") or 0)
    unit = str(stop.get("unit") or ("%" if kind != "conic" else "deg")).lower()
    if unit == "%":
        fraction = value / 100.0
    elif unit == "px":
        fraction = value / length if length else 0.0
    elif unit == "deg":
        fraction = value / 360.0
    elif unit == "rad":
        fraction = math.degrees(value) / 360.0
    elif unit == "turn":
        fraction = value
    elif unit == "grad":
        fraction = value * 0.9 / 360.0
    else:
        fraction = value / 100.0
    return max(0.0, min(1.0, fraction))


def _layer_colors(layer: Dict[str, Any], length: float, kind: str) -> List[Tuple[float, QtGui.QColor]]:
    """一层渐变的色标 → 位置严格递增的 `[(0~1, QColor)]`（含颜色提示 / 同位置硬边 / 重复）。

    * 颜色提示（只有位置没有颜色）：在提示位置插一个前后色的**中间色** ——
      CSS 的提示就是把 50% 混合色挪到那个位置，两个色标时与浏览器逐像素一致；
    * 同一位置的两个色标（CSS 的硬切换）之间让出 `STOP_EPS`，否则 Qt 会覆盖前一个，
      整条渐变退化成纯色（用户 2026-10-28 报的那张图）；
    * `repeating-*`：把一个周期的色标**复制**到铺满整条渐变线（Qt 没有 repeating 语义，
      而复制出来的结果在可见范围内与 CSS 完全一致）。
    """
    stops = list(layer.get("stops") or [])
    if layer.get("repeat") and stops:
        stops = _expand_repeating(stops, length, kind)
    entries: List[Tuple[float, Optional[QtGui.QColor]]] = []
    for stop in stops:
        fraction = _stop_fraction(stop, length, kind)
        if stop.get("hint"):
            entries.append((fraction, None))
        else:
            entries.append((fraction, _qt_color(stop.get("color"), stop.get("alpha", 1.0))))
    resolved: List[Tuple[float, QtGui.QColor]] = []
    for index, (fraction, color) in enumerate(entries):
        if color is None:
            previous = resolved[-1] if resolved else None
            following = next((entries[i][1] for i in range(index + 1, len(entries))
                              if entries[i][1] is not None), None)
            if previous is None or following is None:
                continue
            mixed = QtGui.QColor(
                (previous[1].red() + following.red()) // 2,
                (previous[1].green() + following.green()) // 2,
                (previous[1].blue() + following.blue()) // 2)
            mixed.setAlphaF((previous[1].alphaF() + following.alphaF()) / 2)
            color = mixed
        resolved.append((fraction, color))
    return nudge_stops(resolved)


def _expand_repeating(stops: List[Dict[str, Any]], length: float,
                      kind: str) -> List[Dict[str, Any]]:
    """`repeating-*`：把一个周期（= 最后一个色标的位置）复制到盖满整条渐变线。"""
    last = stops[-1]
    cycle = float(last.get("pos") or 0)
    unit = str(last.get("unit") or "%")
    if cycle <= 0:
        return stops
    # 一条渐变线的总长（与色标同一单位）
    if unit == "%":
        total = 100.0
    elif unit == "px":
        total = max(1.0, float(length))
    elif unit == "deg":
        total = 360.0
    elif unit == "rad":
        total = 2 * math.pi
    elif unit == "turn":
        total = 1.0
    else:
        total = 100.0
    result: List[Dict[str, Any]] = []
    for index in range(min(256, int(math.ceil(total / cycle)) + 1)):
        shift = cycle * index
        for stop in stops:
            position = float(stop.get("pos") or 0) + shift
            if position <= total + 1e-9:
                clone = dict(stop)
                clone["pos"] = position
                result.append(clone)
    while (len(result) > 1
           and abs(float(result[-1].get("pos") or 0) - float(result[-2].get("pos") or 0)) < 1e-9
           and abs(float(result[-1].get("pos") or 0) - total) < 1e-9):
        # 末尾重复落在 total 上的色标（一个周期结束 + 下一个周期开始）留一个就够，
        # 多留一个只会把整组往前挤（`STOP_EPS` 累积到渐变线之外）
        result.pop()
    if not result or abs(float(result[-1].get("pos") or 0) - total) > 1e-9:
        # 结尾补一个色标，正好落在渐变线的末端（否则 Qt 会把最后一段拉伸）
        clone = _cycle_color_at(stops, total % cycle)
        clone["pos"] = total
        result.append(clone)
    return result


def _cycle_color_at(stops: List[Dict[str, Any]], position: float) -> Dict[str, Any]:
    """一个周期里某个位置的颜色（线性插值）—— `repeating-*` 收尾用。"""
    previous = stops[0]
    for stop in stops:
        if float(stop.get("pos") or 0) >= position:
            if stop is previous:
                return dict(stop)
            span = float(stop.get("pos") or 0) - float(previous.get("pos") or 0)
            ratio = 0.0 if span <= 0 else (position - float(previous.get("pos") or 0)) / span
            left = _qt_color(previous.get("color"), previous.get("alpha", 1.0))
            right = _qt_color(stop.get("color"), stop.get("alpha", 1.0))
            mixed = QtGui.QColor(
                int(round(left.red() * (1 - ratio) + right.red() * ratio)),
                int(round(left.green() * (1 - ratio) + right.green() * ratio)),
                int(round(left.blue() * (1 - ratio) + right.blue() * ratio)))
            mixed.setAlphaF(left.alphaF() * (1 - ratio) + right.alphaF() * ratio)
            return {"color": mixed.name(), "alpha": mixed.alphaF(), "aa": False,
                    "hint": False, "pos": position, "unit": previous.get("unit")}
        previous = stop
    return dict(stops[-1])


# 同一位置的两个色标之间留出的间距（占整条渐变的比例）。0.05% 在预览那一百多像素宽上
# 连一个像素都不到，画出来就是 CSS 里那种硬边。
STOP_EPS = 0.0005


def nudge_stops(stops: List[Tuple[float, QtGui.QColor]]) -> List[Tuple[float, QtGui.QColor]]:
    """让色标位置**严格递增**（Qt 的 `setColorAt()` 以位置为键，同位置会覆盖前一个）。

    挤到 100% 之外时整组一起往回挪（最多 `#stops × STOP_EPS`，肉眼看不出来）。
    """
    result = list(stops)
    for index in range(1, len(result)):
        previous = result[index - 1][0]
        if result[index][0] <= previous:
            result[index] = (previous + STOP_EPS, result[index][1])
    overflow = (result[-1][0] - 1.0) if result else 0.0
    if overflow > 0:
        result = [(max(0.0, min(1.0, position - overflow)), color)
                  for position, color in result]
    return result


def gradient_stops(layer: Dict[str, Any]) -> List[Tuple[float, QtGui.QColor]]:
    """渐变层的色标 → **位置严格递增**的 `[(0~1, QColor)]`（按 200×100 的参照盒子换算 px）。"""
    return _layer_colors(layer, float(REFERENCE_WIDTH), layer.get("kind") or "linear")


def _linear_gradient(layer: Dict[str, Any], width: float, height: float,
                     colors: Sequence[Tuple[float, QtGui.QColor]]) -> QtGui.QLinearGradient:
    """按 CSS 的渐变线（过中心、长度要盖住盒子）建 `QLinearGradient`。"""
    angle = math.radians(css_angle(layer, width, height))
    # CSS：0deg 指向上方、顺时针；屏幕坐标 y 向下，所以方向向量是 (sin, -cos)
    dx, dy = math.sin(angle), -math.cos(angle)
    length = abs(width * dx) + abs(height * dy)
    cx, cy = width / 2.0, height / 2.0
    gradient = QtGui.QLinearGradient(
        QtCore.QPointF(cx - dx * length / 2.0, cy - dy * length / 2.0),
        QtCore.QPointF(cx + dx * length / 2.0, cy + dy * length / 2.0))
    for position, color in colors:
        gradient.setColorAt(position, color)
    return gradient


def _radial_geometry(layer: Dict[str, Any], width: float, height: float) -> Tuple[float, float, float, float]:
    """径向渐变的 `(cx, cy, rx, ry)`（px），按 CSS 的 <ending-shape> / <size> 规则算。

    | 写法 | 结果 |
    |---|---|
    | 无尺寸 | `farthest-corner`（默认椭圆，取到最远角） |
    | `closest-side` / `closest-corner` / `farthest-side` / `farthest-corner` | 圆取单一半径、椭圆取 (rx, ry) |
    | 一个长度（`circle 100px` / `25px`） | 圆，半径 = 该长度 |
    | 两个长度（`75px 100px` / `50% 50%`） | 椭圆（`%` 以盒子宽 / 高为基准） |
    """
    def axis(value: Any, unit: Any, size: float) -> float:
        unit = str(unit or "%").lower()
        if unit == "%":
            return float(value or 0) / 100.0 * size
        return float(value or 0)

    cx = axis(layer.get("cx", 50), layer.get("cxUnit"), width)
    cy = axis(layer.get("cy", 50), layer.get("cyUnit"), height)
    left, right = abs(cx), abs(width - cx)
    top, bottom = abs(cy), abs(height - cy)
    keyword = str(layer.get("sizeKw") or "").lower()
    shape = str(layer.get("shape") or "").lower()
    unit_x = str(layer.get("radiusUnitX") or "")
    unit_y = str(layer.get("radiusUnitY") or "")
    radius_x = float(layer.get("radiusX") or 0)
    radius_y = float(layer.get("radiusY") or 0)
    if keyword:
        corners = tuple((x, y) for x, y in ((left, top), (right, top),
                                            (left, bottom), (right, bottom)))
        if keyword in ("closest-side", "farthest-side"):
            pick = min if keyword == "closest-side" else max
            if shape == "circle":
                rx = ry = pick(left, right, top, bottom)
            else:
                rx, ry = pick(left, right), pick(top, bottom)
            return cx, cy, max(0.5, rx), max(0.5, ry)
        # closest-corner / farthest-corner
        pick = min if keyword == "closest-corner" else max
        if shape == "circle":
            rx = ry = pick(math.hypot(x, y) for x, y in corners)
            return cx, cy, max(0.5, rx), max(0.5, ry)
        factor = pick(scale_factors(corners, width, height))
        return cx, cy, max(0.5, width * factor), max(0.5, height * factor)
    if unit_x and radius_x > 0:
        rx = axis(radius_x, unit_x, width)
        ry = axis(radius_y, unit_y, height) if (unit_y and radius_y > 0) else rx
        return cx, cy, max(0.5, rx), max(0.5, ry)
    if shape == "circle":
        rx = ry = max(math.hypot(left, top), math.hypot(right, top),
                      math.hypot(left, bottom), math.hypot(right, bottom))
        return cx, cy, max(0.5, rx), max(0.5, ry)
    # 没有尺寸：`farthest-corner` 的椭圆（保持盒子宽高比，取到最远的角）
    corners = ((left, top), (right, top), (left, bottom), (right, bottom))
    factor = max(scale_factors(corners, width, height))
    if factor <= 0:
        rx = ry = max(math.hypot(x, y) for x, y in corners)
        return cx, cy, max(0.5, rx), max(0.5, ry)
    return cx, cy, max(0.5, width * factor), max(0.5, height * factor)


def scale_factors(corners: Sequence[Tuple[float, float]], width: float,
                  height: float) -> List[float]:
    """椭圆保持**盒子宽高比**时，要刚好碰到每个角所需的缩放系数（CSS 的 corner 关键字用）。"""
    return [math.hypot(x / max(1.0, width), y / max(1.0, height)) for x, y in corners]


def _conic_image(layer: Dict[str, Any], width: int, height: int,
                 colors: Sequence[Tuple[float, QtGui.QColor]]) -> QtGui.QImage:
    """锥形渐变：Qt 5 没有 `QConicalGradient` 的 CSS 语义（默认 `from 0deg at center` 其实有，
    但角度方向与 `at` 位置要自己算），所以逐像素算角度插值 —— 一次 200×24 只有几千个像素。
    """
    image = QtGui.QImage(max(1, width), max(1, height), QtGui.QImage.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.transparent)
    if not colors:
        return image
    start = css_angle(layer, width, height) % 360.0
    cx, cy = _radial_geometry(layer, width, height)[:2]
    stops = colors
    for y in range(max(1, height)):
        dy = y - cy
        for x in range(max(1, width)):
            dx = x - cx
            # 0deg 指向正上方、顺时针
            angle = (math.degrees(math.atan2(dx, -dy)) - start) % 360.0
            fraction = angle / 360.0
            color = _interpolate(stops, fraction)
            image.setPixelColor(x, y, color)
    return image


def _interpolate(stops: Sequence[Tuple[float, QtGui.QColor]], fraction: float) -> QtGui.QColor:
    """按色标插值出一支颜色（不在色标上时线性过渡）。"""
    if not stops:
        return QtGui.QColor(0, 0, 0, 0)
    if fraction <= stops[0][0]:
        return QtGui.QColor(stops[0][1])
    if fraction >= stops[-1][0]:
        return QtGui.QColor(stops[-1][1])
    for index in range(1, len(stops)):
        left, right = stops[index - 1], stops[index]
        if fraction <= right[0]:
            span = right[0] - left[0]
            ratio = 0.0 if span <= 0 else (fraction - left[0]) / span
            def mix(get: str) -> int:
                return int(round(getattr(left[1], get)() * (1 - ratio)
                                 + getattr(right[1], get)() * ratio))
            color = QtGui.QColor(mix("red"), mix("green"), mix("blue"))
            color.setAlphaF(left[1].alphaF() * (1 - ratio) + right[1].alphaF() * ratio)
            return color
    return QtGui.QColor(stops[-1][1])


def _layer_image(layer: Dict[str, Any], width: int, height: int) -> Optional[QtGui.QImage]:
    """一层渐变 → 一张 `width × height` 的图（锥形靠逐像素画，其余用 QPainter）。"""
    kind = layer.get("kind") or "linear"
    if kind == "conic":
        length = 360.0
        colors = _layer_colors(layer, length, "conic")
        return _conic_image(layer, width, height, colors)
    if kind == "radial":
        cx, cy, rx, ry = _radial_geometry(layer, width, height)
        colors = _layer_colors(layer, max(rx, ry), "radial")
    else:
        angle = math.radians(css_angle(layer, width, height))
        dx, dy = math.sin(angle), -math.cos(angle)
        length = abs(width * dx) + abs(height * dy)
        colors = _layer_colors(layer, length, "linear")
    return _gradient_image(layer, width, height, colors)


def _gradient_image(layer: Dict[str, Any], width: int, height: int,
                    colors: Sequence[Tuple[float, QtGui.QColor]]) -> Optional[QtGui.QImage]:
    """用 QPainter 把一层渐变画进一张图（线性 / 径向，含椭圆）。"""
    image = QtGui.QImage(max(1, width), max(1, height), QtGui.QImage.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.transparent)
    if not colors:
        return None
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.setPen(QtCore.Qt.NoPen)
    kind = layer.get("kind") or "linear"
    if kind == "radial":
        cx, cy, rx, ry = _radial_geometry(layer, width, height)
        gradient = QtGui.QRadialGradient(QtCore.QPointF(0, 0), 1.0)
        for position, color in colors:
            gradient.setColorAt(position, color)
        painter.translate(cx, cy)
        painter.scale(max(0.01, rx), max(0.01, ry))
        painter.setBrush(QtGui.QBrush(gradient))
        painter.drawRect(QtCore.QRectF(-1e4, -1e4, 2e4, 2e4))
    else:
        painter.setBrush(QtGui.QBrush(_linear_gradient(layer, width, height, colors)))
        painter.drawRect(QtCore.QRectF(0, 0, width, height))
    painter.end()
    return image


REFERENCE_WIDTH = 200      # `gradient_stops()` 换算 px 色标时用的参照盒子（与站上的示例一致）
REFERENCE_HEIGHT = 100


def _length(value: Any, unit: Any, size: float) -> float:
    """`25px` / `50%` / `auto` → 像素值（`auto` 表示「跟盒子一样大」）。"""
    unit = str(unit or "%").lower()
    if unit in ("auto", "cover", "contain"):
        return size
    if unit == "%":
        return float(value or 0) / 100.0 * size
    return float(value or 0)


def _layer_rect(layer: Dict[str, Any], rect: QtCore.QRect) -> QtCore.QRect:
    """背景块自己的位置与尺寸（`background-position` / `-size`）。

    `position` 是**背景块左上角**（绝对长度）或「按百分比对齐」（`50% 50%` = 居中）。
    """
    width, height = rect.width(), rect.height()
    tile_w = _length(layer.get("sizeW"), layer.get("sizeUnitW"), width) if layer.get("sizeSet") else width
    tile_h = _length(layer.get("sizeH"), layer.get("sizeUnitH"), height) if layer.get("sizeSet") else height
    tile_w = max(1.0, tile_w)
    tile_h = max(1.0, tile_h)
    if not layer.get("posSet"):
        return QtCore.QRect(0, 0, int(round(tile_w)), int(round(tile_h)))
    unit_x = str(layer.get("posUnitX") or "%")
    unit_y = str(layer.get("posUnitY") or "%")
    if unit_x == "%":
        left = float(layer.get("posX") or 0) / 100.0 * (width - tile_w)
    else:
        left = float(layer.get("posX") or 0)
    if unit_y == "%":
        top = float(layer.get("posY") or 0) / 100.0 * (height - tile_h)
    else:
        top = float(layer.get("posY") or 0)
    return QtCore.QRect(int(round(left)), int(round(top)),
                        int(round(tile_w)), int(round(tile_h)))


def _inner_rect(layer: Dict[str, Any], rect: QtCore.QRect, state: Dict[str, Any]) -> QtCore.QRect:
    """`background-origin`：以哪个框为基准（padding-box 默认 / border-box / content-box）。"""
    origin = str(layer.get("origin") or "padding-box")
    border = int(round(float(state.get("borderWidth") or 0)))
    pad_y = int(round(float(state.get("padY") or 0)))
    pad_x = int(round(float(state.get("padX") or 0)))
    if origin == "border-box":
        return rect.adjusted(-border, -border, border, border)
    if origin == "content-box":
        return rect.adjusted(border + pad_x, border + pad_y,
                             -(border + pad_x), -(border + pad_y))
    return rect


def render_background(painter: QtGui.QPainter, rect: QtCore.QRect, state: Dict[str, Any],
                      clip_path: Optional[QtGui.QPainterPath] = None,
                      glyph_path: Optional[QtGui.QPainterPath] = None) -> None:
    """按 CSS `background` 的语义把整块背景画进 `rect`。

    顺序照 CSS：**纯底色在最下**，`bgLayers[0]` 在最上；每层各自 position / size / repeat /
    origin / clip，`repeating-*` 按一个周期平铺（以前只画第一层线性渐变，多层与重复全丢，
    radial / conic 更是直接画成纯色 —— 用户 2026-10-28 对照 JackBlock 那两页报的）。

    `glyph_path` 是「这一块上的文字路径」，供 `background-clip: text` 用（文字才会透出渐变）。
    """
    painter.save()
    if clip_path is not None:
        painter.setClipPath(clip_path, QtCore.Qt.IntersectClip)
    painter.fillRect(rect, _qt_color(state.get("bgSolid"), state.get("bgSolidAlpha", 1.0)))
    layers = list(state.get("bgLayers") or [])
    for layer in reversed(layers):              # 列表里 index 0 在最上，所以从后往前画
        _draw_layer(painter, rect, layer, state, glyph_path)
    painter.restore()


def _draw_layer(painter: QtGui.QPainter, rect: QtCore.QRect, layer: Dict[str, Any],
                state: Dict[str, Any], glyph_path: Optional[QtGui.QPainterPath] = None) -> None:
    """画一层渐变（含平铺与 origin/clip）。"""
    clip = str(layer.get("clip") or "")
    if clip == "text" and glyph_path is not None:
        painter.save()
        painter.setClipPath(glyph_path, QtCore.Qt.IntersectClip)
        _paint_layer_image(painter, rect, layer, state)
        painter.restore()
        return
    painter.save()
    if clip in style_state.BOX_KEYWORDS:
        painter.setClipRect(_inner_rect({"origin": clip}, rect, state),
                            QtCore.Qt.IntersectClip)
    _paint_layer_image(painter, rect, layer, state)
    painter.restore()


def _paint_layer_image(painter: QtGui.QPainter, rect: QtCore.QRect, layer: Dict[str, Any],
                       state: Dict[str, Any]) -> None:
    """把一层渐变按 position / size / repeat 铺到 `rect` 上。"""
    area = _inner_rect(layer, rect, state)
    tile = _layer_rect(layer, area)
    if tile.width() <= 0 or tile.height() <= 0:
        return
    image = _layer_image(layer, tile.width(), tile.height())
    if image is None:
        return
    mode = str(layer.get("repeatMode") or "")
    if not mode and (layer.get("repeat") or layer.get("sizeSet") or layer.get("posSet")):
        # CSS：`background-image` 默认 `repeat` —— 背景块比盒子小、或者被挪了位置时，
        # 都要往四周铺满（`0 0/20px 20px` 与 `210px -10px` 都是这个道理）
        mode = "repeat"
    painter.save()
    painter.translate(area.left(), area.top())
    if mode in ("repeat", "repeat-x", "repeat-y", "space", "round"):
        # 平铺：拿「一个周期」的图当纹理画刷（Qt 会按画刷原点往**四周**重复，
        # 所以位置写成负数 / 超出盒子时也能正确铺满）
        brush = QtGui.QBrush(image)
        brush.setTransform(QtGui.QTransform().translate(tile.left(), tile.top()))
        painter.setBrush(brush)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrushOrigin(0, 0)
        band = QtCore.QRect(0, 0, area.width(), area.height())
        if mode == "repeat-x":
            band = QtCore.QRect(0, tile.top(), area.width(), tile.height())
            painter.setClipRect(band, QtCore.Qt.IntersectClip)
        elif mode == "repeat-y":
            band = QtCore.QRect(tile.left(), 0, tile.width(), area.height())
            painter.setClipRect(band, QtCore.Qt.IntersectClip)
        painter.drawRect(band)
    else:
        painter.drawImage(tile, image)
    painter.restore()


def _glyph_path(text: Any, font: QtGui.QFont, inner: QtCore.QRect,
                align_left: bool = False) -> Optional[QtGui.QPainterPath]:
    """文字 → 路径（`background-clip: text` 用它当裁剪区）。"""
    lines = [line for line in str(text or "").split("\n")]
    if not any(line.strip() for line in lines):
        return None
    metrics = QtGui.QFontMetricsF(font)
    path = QtGui.QPainterPath()
    y = inner.center().y() - metrics.height() * len(lines) / 2 + metrics.ascent()
    for line in lines:
        width = metrics.width(line)
        x = inner.left() if align_left else inner.center().x() - width / 2
        path.addText(QtCore.QPointF(x, y), font, line)
        y += metrics.height()
    return path


def state_brush(state: Dict[str, Any], rect: QtCore.QRect) -> QtGui.QBrush:
    """状态 → 画刷：最上层渐变（按 `rect` 的尺寸算角度）或纯底色。

    `render_background()` 才是完整实现（多层 / 平铺 / radial / conic），这里保留是因为
    `draw_state_box()` 之外还有调用方（单测）按「一支画刷」的接口用。
    """
    layers = state.get("bgLayers") or []
    for layer in layers:
        kind = layer.get("kind") or "linear"
        if kind != "linear":
            image = _layer_image(layer, max(1, rect.width()), max(1, rect.height()))
            return QtGui.QBrush(image) if image is not None else QtGui.QBrush(
                _qt_color(state.get("bgSolid"), state.get("bgSolidAlpha", 1.0)))
        angle = math.radians(css_angle(layer, rect.width(), rect.height()))
        dx, dy = math.sin(angle), -math.cos(angle)
        length = abs(rect.width() * dx) + abs(rect.height() * dy)
        colors = _layer_colors(layer, length, "linear")
        gradient = QtGui.QLinearGradient(
            QtCore.QPointF(rect.center().x() - dx * length / 2,
                           rect.center().y() - dy * length / 2),
            QtCore.QPointF(rect.center().x() + dx * length / 2,
                           rect.center().y() + dy * length / 2))
        for position, color in colors:
            gradient.setColorAt(position, color)
        return QtGui.QBrush(gradient)
    return QtGui.QBrush(_qt_color(state.get("bgSolid"), state.get("bgSolidAlpha", 1.0)))


def draw_state_box(painter: QtGui.QPainter, rect: QtCore.QRect, state: Dict[str, Any],
                   text: str, font_scale: float = 1.0,
                   align_left: bool = False) -> None:
    """按状态画一个方块（底色 / 渐变 / 边框 / 圆角 / 文字）。

    `align_left` 给歌词的原文 / 译文用：站上那两栏是 `text-align: start`（左对齐），
    色块（Songbox / 标签格）才是居中的。
    """
    alignment = ((QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter) if align_left
                 else QtCore.Qt.AlignCenter)
    painter.save()
    opacity = float(state.get("opacity", 1.0))
    painter.setOpacity(painter.opacity() * max(0.0, min(1.0, opacity)))
    radius = float(state.get("radius") or 0)
    path = QtGui.QPainterPath()
    path.addRoundedRect(QtCore.QRectF(rect), radius, radius)
    for shadow in state.get("boxShadows") or []:
        if shadow.get("inset"):
            continue
        blur = max(0.0, float(shadow.get("blur") or 0))
        spread = float(shadow.get("spread") or 0)
        steps = max(1, min(8, int(math.ceil(blur / 2))))
        levels = range(steps, -1, -1) if blur else (0,)
        for step in levels:
            expansion = spread + blur * step / steps
            shadow_rect = QtCore.QRectF(rect).adjusted(
                -expansion, -expansion, expansion, expansion)
            shadow_rect.translate(float(shadow.get("x") or 0),
                                  float(shadow.get("y") or 0))
            shadow_path = QtGui.QPainterPath()
            shadow_path.addRoundedRect(shadow_rect, max(0.0, radius + expansion),
                                       max(0.0, radius + expansion))
            color = _qt_color(shadow.get("color"), shadow.get("alpha", 1.0))
            if blur:
                color.setAlphaF(color.alphaF() * (1.0 - step / (steps + 1)))
            painter.fillPath(shadow_path, color)
    # 字体要先算出来：`background-clip: text` 需要按这一块的文字取字形路径
    font = QtGui.QFont(painter.font())
    font.setPixelSize(max(6, int(float(state.get("fontSize") or 12) * font_scale)))
    font.setWeight(_qt_font_weight(state.get("weight")))
    if state.get("letterSpacing"):
        font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, float(state["letterSpacing"]))
    pad_x = float(state.get("padX") or 0)
    inner = rect.adjusted(int(pad_x), 2, -int(pad_x), -2)
    glyphs = _glyph_path(text, font, inner, align_left)
    # 背景走完整的 CSS 语义（多层 / position / size / repeat / origin / clip / 三种渐变）
    painter.save()
    painter.setClipPath(path, QtCore.Qt.IntersectClip)
    render_background(painter, rect, state, path, glyphs)
    painter.restore()
    width = float(state.get("borderWidth") or 0)
    if width > 0 and (state.get("borderStyle") or "solid") != "none":
        pen = QtGui.QPen(_qt_color(state.get("borderColor"), state.get("borderAlpha", 1.0))
                         if not state.get("borderCurrent", True)
                         else _qt_color(state.get("color"), 1.0))
        pen.setWidthF(max(1.0, width))
        pen.setStyle(BORDER_STYLES.get(state.get("borderStyle") or "solid", QtCore.Qt.SolidLine))
        painter.setPen(pen)
        painter.drawPath(path)
    painter.setFont(font)
    for shadow in state.get("textShadows") or []:
        x = float(shadow.get("x") or 0)
        y = float(shadow.get("y") or 0)
        blur = max(0.0, float(shadow.get("blur") or 0))
        offsets = [(x, y)]
        if blur:
            offsets.extend((x + math.cos(angle) * blur, y + math.sin(angle) * blur)
                           for angle in (0, math.pi / 2, math.pi, 3 * math.pi / 2))
        color = _qt_color(shadow.get("color"), shadow.get("alpha", 1.0) /
                          len(offsets))
        painter.setPen(color)
        for dx, dy in offsets:
            painter.drawText(inner.translated(int(dx), int(dy)),
                             alignment | QtCore.Qt.TextWordWrap, text)
    painter.setPen(_qt_color(state.get("color"), state.get("colorAlpha", 1.0)))
    painter.drawText(inner, alignment | QtCore.Qt.TextWordWrap, text)
    painter.restore()


class StylePreview(QtWidgets.QWidget):
    """把当前状态画出来（点「预览显示范围」控制只看哪一段）。

    它也是辅助工具的画布（测量 / 参照物，见 utils/ui/aux_tools.py）。
    """

    aux_changed = QtCore.pyqtSignal(str)          # 辅助工具状态变了（"ok" / "full"）
    aux_exit = QtCore.pyqtSignal()                # 右键要求退出工具模式

    def __init__(self, parent=None):
        super().__init__(parent)
        # 外面套了滚动区（见 StylePanel._build_ui）：高度跟着给的空间走，
        # 宽度/高度不够时靠 minimumSize 出滚动条，不再把内容裁掉
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setMinimumSize(400, 0)
        self.states: List[Dict[str, Any]] = []
        self.tpl_states: Dict[str, Dict[str, Any]] = {}
        self.view = "all"
        self.canvas = 560
        self.gap = 22
        self.active = -1
        self._aux = None
        self._aux_space = "preview"

    def set_aux(self, aux, space: str = "preview") -> None:
        self._aux = aux
        self._aux_space = space
        self.update()

    def update_states(self, states: Sequence[Dict[str, Any]],
                      tpl_states: Dict[str, Dict[str, Any]], view: str,
                      canvas: int, gap: int, active: Any) -> None:
        self.states = list(states)
        self.tpl_states = tpl_states
        self.view = view
        self.canvas = canvas
        self.gap = gap
        self.active = active
        self.setMinimumSize(self.content_size())
        self.updateGeometry()
        self.update()

    # —— 要多大才画得下 ——
    def _pill_sizes(self) -> List[Tuple[float, float]]:
        """每颗色块的 (宽, 高)，按当前画布宽算（CSS 里 `width:100%; max-width:450px`）。

        三块是**竖着**排的（与站内 Songbox 一致）：宽度各自跟着画布走，
        不需要再为「一行装不下」而一起缩窄。
        """
        return [(min(float(state.get("maxWidth") or self.canvas),
                     self.canvas * float(state.get("width") or 100) / 100),
                 float(state.get("height") or 24))
                for state in self.states]

    def _songbox_size(self) -> Tuple[float, float]:
        """竖排三块整体占的 (宽, 高)。"""
        pills = self._pill_sizes()
        if not pills:
            return 0.0, 0.0
        return (max(width for width, _height in pills),
                sum(height for _width, height in pills) + PILL_GAP * (len(pills) - 1))

    def _intro_height(self) -> float:
        label = self.tpl_states.get("introLabel")
        if label is None:
            return 0.0
        return max(28, int(float(label.get("height") or 24)) * 2 + 8)

    def _lyrics_height(self) -> float:
        """歌词段的高度：容器**上下内边距** + 每行 `.Lyrics-line` 的高度。

        站上容器（`.Lyrics`）的 padding 默认是 0，行高由正文行高撑开；这里同样把
        `padY` 算进去，改内边距时预览会跟着长高（不再被一个固定高度框死）。
        """
        container = self.tpl_states.get("lyrContainer")
        if container is None:
            return 0.0
        pad_y = max(0.0, float(container.get("padY") or 0))
        return float(LYRIC_LINE_HEIGHT) * len(LYRIC_ROWS) + pad_y * 2

    def content_size(self) -> QtCore.QSize:
        """画下当前内容真正需要的尺寸（外面按这个出滚动条）。"""
        songbox_width, songbox_height = self._songbox_size()
        width = songbox_width
        rows: List[float] = []
        if self.view in ("all", "songbox"):
            # 选中那块会向外画 3px 的虚线框
            rows.append(songbox_height + 6)
        if self.view in ("all", "intro"):
            rows.append(self._intro_height())
        if self.view in ("all", "lyrics"):
            rows.append(self._lyrics_height())
        height = sum(rows) + self.gap * max(0, len(rows) - 1)
        return QtCore.QSize(int(max(width, 400)) + CANVAS_PAD * 2,
                            int(height) + CANVAS_PAD * 2)

    def sizeHint(self) -> QtCore.QSize:
        return self.content_size()

    def minimumSizeHint(self) -> QtCore.QSize:
        return self.content_size()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QtGui.QColor("#f7f8fc"))
        area = self.rect().adjusted(CANVAS_PAD, CANVAS_PAD, -CANVAS_PAD, -CANVAS_PAD)
        try:
            self._paint_content(painter, area)
        finally:
            # 辅助工具画在最上面，不受预览内容是否存在影响
            if self._aux is not None:
                self._aux.paint(painter, self._aux_space)

    def _paint_content(self, painter: QtGui.QPainter, area: QtCore.QRect) -> None:
        y = area.top()
        if not self.states:
            painter.setPen(QtGui.QColor("#72777d"))
            painter.drawText(area, QtCore.Qt.AlignCenter, "样式编辑器尚未启动")
            return
        if self.view in ("all", "songbox"):
            y += self._draw_songbox(painter, area, y) + self.gap
        if self.view in ("all", "intro"):
            y += self._draw_intro(painter, area, y) + self.gap
        if self.view in ("all", "lyrics"):
            self._draw_lyrics(painter, area, y)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._aux is not None and self._aux.mode:
            if event.button() == QtCore.Qt.RightButton:
                self.aux_exit.emit()
            elif event.button() == QtCore.Qt.LeftButton:
                self.aux_changed.emit(self._aux.click(self._aux_space,
                                                     (event.pos().x(), event.pos().y())))
            return
        super().mousePressEvent(event)

    # —— 各段 ——
    def songbox_rects(self, area: QtCore.QRect, top: int) -> List[QtCore.QRect]:
        """三块色块的矩形：竖着排，每块都在 `area` 里**居中**。

        站上 Songbox 的外层是 `display:flex; justify-content:center`（声库色块
        `width:100%; max-width:450px`），所以三块跟下面的 Introduction / 歌词
        共用同一条中线；贴左画会让预览和实际输出对不上。
        """
        rects: List[QtCore.QRect] = []
        y = top
        for width, height in self._pill_sizes():
            rects.append(_centered_rect(area, int(y), int(width), int(height)))
            y += height + PILL_GAP
        return rects

    def _draw_songbox(self, painter: QtGui.QPainter, area: QtCore.QRect, top: int) -> int:
        rects = self.songbox_rects(area, top)
        for index, rect in enumerate(rects):
            state = self.states[index]
            label = state.get("text") or style_state.RECT_LABELS[index]
            draw_state_box(painter, rect, state, label, font_scale=0.85)
            if index == self.active:
                painter.setPen(QtGui.QPen(QtGui.QColor("#5b6cff"), 1, QtCore.Qt.DashLine))
                painter.drawRect(rect.adjusted(-3, -3, 3, 3))
        if not rects:
            return 0
        return int(max(0, rects[-1].bottom() + 1 - top))

    def _draw_intro(self, painter: QtGui.QPainter, area: QtCore.QRect, top: int) -> int:
        label = self.tpl_states.get("introLabel")
        if label is None:
            return 0
        height = int(self._intro_height())
        label_width = min(72, area.width())
        list_width = min(300, max(0, area.width() - label_width - 8))
        total_width = label_width + (8 if list_width else 0) + list_width
        content = _centered_rect(area, top, total_width, height)
        label_rect = QtCore.QRect(content.left(), top, label_width, height)
        draw_state_box(painter, label_rect, label, "演唱", font_scale=0.85)
        list_rect = QtCore.QRect(content.left() + label_width + 8, top, list_width, height)
        painter.setPen(QtGui.QPen(QtGui.QColor("#b0c4de")))
        painter.setBrush(QtGui.QColor("#ffffff"))
        painter.drawRect(list_rect)
        painter.setPen(QtGui.QColor("#1d2130"))
        for index, (_title, value) in enumerate(INTRO_ROWS):
            painter.drawText(QtCore.QRect(list_rect.left() + 6, top + 4 + index * 20,
                                          list_rect.width() - 12, 18),
                             QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, value)
        return height

    def lyrics_rects(self, area: QtCore.QRect, top: int) -> Dict[str, Any]:
        """歌词段的矩形：容器 + 每行 `.Lyrics-line` 的（原文, 译文）两块。

        站上 `{{LyricsKai}}` 渲染出来是

            <div class="Lyrics">                 ← 容器（containerstyle）
              <div class="Lyrics-line">          ← 一行：原文 + 它的译文
                <div class="Lyrics-original">    ← lstyle，桌面端占 49.85%
                <div class="Lyrics-translated">  ← rstyle，占 49.85%，**与原文同一行**

        所以原文与译本是**并排**的（`body.main-sm` 实测：两者 y 相同、各占一半宽），
        行与行之间才上下堆叠；以前画成「原文一行、译文一行」上下两块，跟站上对不上
        （用户 2026-10-16 报「歌词的显示还是有问题」）。
        """
        container = self.tpl_states.get("lyrContainer")
        if container is None:
            return {}
        height = int(self._lyrics_height())
        rect = _centered_rect(area, top, LYRICS_WIDTH, height)
        pad_x = max(0, int(float(container.get("padX") or 0)))
        pad_y = max(0, int(float(container.get("padY") or 0)))
        half = max(1, int((rect.width() - pad_x * 2) * LYRIC_HALF))
        lines = []
        for index, (original_text, translated_text) in enumerate(LYRIC_ROWS):
            line_top = rect.top() + pad_y + index * LYRIC_LINE_HEIGHT
            lines.append((QtCore.QRect(rect.left() + pad_x, line_top,
                                       half, LYRIC_LINE_HEIGHT),
                          QtCore.QRect(rect.left() + pad_x + half, line_top,
                                       half, LYRIC_LINE_HEIGHT),
                          original_text, translated_text))
        return {"container": rect, "lines": lines}

    def _draw_lyrics(self, painter: QtGui.QPainter, area: QtCore.QRect, top: int) -> int:
        raw_states = self.tpl_states
        container = raw_states.get("lyrContainer")
        original = raw_states.get("lyrOrig")
        translated = raw_states.get("lyrTrans")
        if container is None or original is None or translated is None:
            return 0
        # 原文 / 译文没自己设文字颜色时跟着容器走（站上就是继承 `.Lyrics` 的 color），
        # 否则把容器设成白字、预览还是黑的（用户 2026-10-22 报）。
        states = style_state.resolve_inherited_states(raw_states)
        original = states.get("lyrOrig", original)
        translated = states.get("lyrTrans", translated)
        layout = self.lyrics_rects(area, top)
        rect = layout["container"]
        draw_state_box(painter, rect, container, "")
        painter.save()
        painter.setOpacity(painter.opacity() * max(
            0.0, min(1.0, float(container.get("opacity", 1.0)))))
        for original_rect, translated_rect, original_text, translated_text in layout["lines"]:
            draw_state_box(painter, original_rect, original, original_text,
                           font_scale=0.9, align_left=True)
            draw_state_box(painter, translated_rect, translated, translated_text,
                           font_scale=0.9, align_left=True)
        painter.restore()
        return int(self._lyrics_height())
