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


def state_brush(state: Dict[str, Any], rect: QtCore.QRect) -> QtGui.QBrush:
    """状态 → 画刷：有线性渐变就用它，否则用纯底色。"""
    layers = state.get("bgLayers") or []
    for layer in layers:
        if (layer.get("kind") or "linear") == "linear":
            angle = float(layer.get("angle") or 0) % 360
            # 用角度决定渐变方向：0° 向上，90° 向右，180° 向下…
            radians = math.radians(angle - 90)
            dx = math.cos(radians) * rect.width() / 2
            dy = math.sin(radians) * rect.height() / 2
            gradient = QtGui.QLinearGradient(
                QtCore.QPointF(rect.center().x() - dx, rect.center().y() - dy),
                QtCore.QPointF(rect.center().x() + dx, rect.center().y() + dy))
            for position, color in gradient_stops(layer):
                gradient.setColorAt(position, color)
            return QtGui.QBrush(gradient)
    return QtGui.QBrush(_qt_color(state.get("bgSolid"), state.get("bgSolidAlpha", 1.0)))


# 同一位置的两个色标之间留出的间距（占整条渐变的比例）。0.05% 在预览那一百多像素宽上
# 连一个像素都不到，画出来就是 CSS 里那种硬边。
STOP_EPS = 0.0005


def gradient_stops(layer: Dict[str, Any]) -> List[Tuple[float, QtGui.QColor]]:
    """渐变层的色标 → **位置严格递增**的 `[(0~1, QColor)]`。

    CSS 允许两个色标落在同一个位置（`linear-gradient(135deg, #02013d 60%, #d7dee6 60%)`：
    60% 之前是深蓝、之后是浅灰，中间是硬切换），而 Qt 的
    `QLinearGradient.setColorAt()` 把色标存在以位置为键的表里 —— **同位置会覆盖前一个**，
    整条渐变最后只剩最后一个颜色。用户 2026-10-28 报的就是这个：样式页预览里深蓝底纹
    整片变成浅灰，白字看不见了（站上是正常的，因为浏览器按 CSS 画硬边）。

    所以这里给同位置的色标让出 `STOP_EPS` 的间距；挤到末端之外时把整组一起往回挪
    （最多 `#stops × STOP_EPS`，肉眼看不出来），位置仍然严格递增。
    """
    stops: List[Tuple[float, QtGui.QColor]] = []
    for stop in layer.get("stops") or []:
        position = max(0.0, min(1.0, float(stop.get("pos", 0) or 0) / 100))
        stops.append((position, _qt_color(stop.get("color"), stop.get("alpha", 1.0))))
    for index in range(1, len(stops)):
        previous = stops[index - 1][0]
        if stops[index][0] <= previous:
            stops[index] = (previous + STOP_EPS, stops[index][1])
    overflow = (stops[-1][0] - 1.0) if stops else 0.0
    if overflow > 0:
        stops = [(max(0.0, min(1.0, position - overflow)), color)
                 for position, color in stops]
    return stops


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
    painter.fillPath(path, state_brush(state, rect))
    width = float(state.get("borderWidth") or 0)
    if width > 0 and (state.get("borderStyle") or "solid") != "none":
        pen = QtGui.QPen(_qt_color(state.get("borderColor"), state.get("borderAlpha", 1.0))
                         if not state.get("borderCurrent", True)
                         else _qt_color(state.get("color"), 1.0))
        pen.setWidthF(max(1.0, width))
        pen.setStyle(BORDER_STYLES.get(state.get("borderStyle") or "solid", QtCore.Qt.SolidLine))
        painter.setPen(pen)
        painter.drawPath(path)
    font = QtGui.QFont(painter.font())
    font.setPixelSize(max(6, int(float(state.get("fontSize") or 12) * font_scale)))
    font.setWeight(_qt_font_weight(state.get("weight")))
    if state.get("letterSpacing"):
        font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, float(state["letterSpacing"]))
    painter.setFont(font)
    pad_x = float(state.get("padX") or 0)
    inner = rect.adjusted(int(pad_x), 2, -int(pad_x), -2)
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
