"""「样式」页左边的实时预览：三个颜色块 + Introduction + 歌词。

只做视觉近似（Qt 画不出 CSS 的 box-shadow / 复杂渐变），够用来对着封面调色即可；
真正的输出永远是 utils.ui.style_state 生成的 wikitext 文本。
"""
from typing import Any, Dict, List, Optional, Sequence, Tuple

import math

from PyQt5 import QtCore, QtGui, QtWidgets

from utils.ui import style_state

# 预览里用的示例文字（与原 html 版预览块一致）
INTRO_ROWS = (("演唱", "初音未来"), ("P主", "P主"))
LYRIC_ROWS = ("原文歌词示例文字", "中文译文示例文字")
LYRICS_HEIGHT = 76          # 歌词段固定高度（画的时候和算尺寸的时候用同一个常量）
CANVAS_PAD = 10             # paintEvent 里四周留的白（尺寸计算要加上）
PILL_GAP = 8                # 三块之间的间距（与画的时候一致）


def _qt_color(hex_value: Any, alpha: float = 1.0) -> QtGui.QColor:
    color = QtGui.QColor(style_state.norm_hex(hex_value) or "#ffffff")
    color.setAlphaF(max(0.0, min(1.0, float(alpha))))
    return color


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
            for stop in layer.get("stops") or []:
                position = max(0.0, min(1.0, float(stop.get("pos", 0)) / 100))
                gradient.setColorAt(position, _qt_color(stop.get("color"), stop.get("alpha", 1.0)))
            return QtGui.QBrush(gradient)
    return QtGui.QBrush(_qt_color(state.get("bgSolid"), state.get("bgSolidAlpha", 1.0)))


def draw_state_box(painter: QtGui.QPainter, rect: QtCore.QRect, state: Dict[str, Any],
                   text: str, font_scale: float = 1.0) -> None:
    """按状态画一个方块（底色 / 渐变 / 边框 / 圆角 / 文字）。"""
    painter.save()
    opacity = float(state.get("opacity") or 1)
    painter.setOpacity(max(0.05, min(1.0, opacity)))
    radius = float(state.get("radius") or 0)
    path = QtGui.QPainterPath()
    path.addRoundedRect(QtCore.QRectF(rect), radius, radius)
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
    font.setWeight(int(float(state.get("weight") or 400)))
    if state.get("letterSpacing"):
        font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, float(state["letterSpacing"]))
    painter.setFont(font)
    painter.setPen(_qt_color(state.get("color"), state.get("colorAlpha", 1.0)))
    pad_x = float(state.get("padX") or 0)
    inner = rect.adjusted(int(pad_x), 2, -int(pad_x), -2)
    painter.drawText(inner, QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap, text)
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
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.setMinimumSize(400, 200)
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
        """每颗色块的 (宽, 高)，按当前画布宽/间距算。

        三块是同一行里的项目（CSS 里 `width:100%; max-width:450px` 跟着容器走），
        所以一行装不下时一起等比缩窄——以前不缩，第三块直接跑到画布外面、根本看不见。
        """
        sizes: List[List[float]] = []
        for state in self.states:
            width = min(float(state.get("maxWidth") or self.canvas),
                        self.canvas * float(state.get("width") or 100) / 100)
            sizes.append([width, float(state.get("height") or 24)])
        gaps = PILL_GAP * max(0, len(sizes) - 1)
        wanted = sum(size[0] for size in sizes)
        if wanted > 0 and wanted + gaps > self.canvas:
            room = max(float(self.canvas) - gaps, 40.0)
            scale = room / wanted
            for size in sizes:
                size[0] = size[0] * scale
        return [(width, height) for width, height in sizes]

    def _intro_height(self) -> float:
        label = self.tpl_states.get("introLabel")
        if label is None:
            return 0.0
        return max(28, int(float(label.get("height") or 24)) * 2 + 8)

    def content_size(self) -> QtCore.QSize:
        """画下当前内容真正需要的尺寸（外面按这个出滚动条）。"""
        pills = self._pill_sizes()
        width = sum(size[0] for size in pills) + PILL_GAP * max(0, len(pills) - 1)
        rows: List[float] = []
        if self.view in ("all", "songbox"):
            # 选中那块会向外画 3px 的虚线框
            rows.append((max([size[1] for size in pills]) if pills else 0) + 6)
        if self.view in ("all", "intro"):
            rows.append(self._intro_height())
        if self.view in ("all", "lyrics"):
            rows.append(LYRICS_HEIGHT if self.tpl_states.get("lyrContainer") else 0)
        height = sum(rows) + self.gap * max(0, len(rows) - 1)
        return QtCore.QSize(int(max(width, 400)) + CANVAS_PAD * 2,
                            int(max(height, 120)) + CANVAS_PAD * 2)

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
    def _draw_songbox(self, painter: QtGui.QPainter, area: QtCore.QRect, top: int) -> int:
        heights = []
        pills = []
        x = area.left()
        for index, (width, height) in enumerate(self._pill_sizes()):
            state = self.states[index]
            rect = QtCore.QRect(int(x), top, int(width), int(height))
            label = state.get("text") or style_state.RECT_LABELS[index]
            draw_state_box(painter, rect, state, label, font_scale=0.85)
            if index == self.active:
                painter.setPen(QtGui.QPen(QtGui.QColor("#5b6cff"), 1, QtCore.Qt.DashLine))
                painter.drawRect(rect.adjusted(-3, -3, 3, 3))
            pills.append(rect)
            x += width + PILL_GAP
            heights.append(height)
        return int(max(heights) if heights else 0)

    def _draw_intro(self, painter: QtGui.QPainter, area: QtCore.QRect, top: int) -> int:
        label = self.tpl_states.get("introLabel")
        if label is None:
            return 0
        height = int(self._intro_height())
        label_width = 72
        label_rect = QtCore.QRect(area.left(), top, label_width, height)
        draw_state_box(painter, label_rect, label, "演唱", font_scale=0.85)
        list_rect = QtCore.QRect(area.left() + label_width + 8, top, 300, height)
        painter.setPen(QtGui.QPen(QtGui.QColor("#b0c4de")))
        painter.setBrush(QtGui.QColor("#ffffff"))
        painter.drawRect(list_rect)
        painter.setPen(QtGui.QColor("#1d2130"))
        for index, (_title, value) in enumerate(INTRO_ROWS):
            painter.drawText(QtCore.QRect(list_rect.left() + 6, top + 4 + index * 20,
                                          list_rect.width() - 12, 18),
                             QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, value)
        return height

    def _draw_lyrics(self, painter: QtGui.QPainter, area: QtCore.QRect, top: int) -> int:
        container = self.tpl_states.get("lyrContainer")
        original = self.tpl_states.get("lyrOrig")
        translated = self.tpl_states.get("lyrTrans")
        if container is None or original is None or translated is None:
            return 0
        height = LYRICS_HEIGHT
        rect = QtCore.QRect(area.left(), top, 380, height)
        draw_state_box(painter, rect, container, "")
        row_height = (height - 12) // 2
        for index, (state, text) in enumerate(((original, LYRIC_ROWS[0]),
                                               (translated, LYRIC_ROWS[1]))):
            row = QtCore.QRect(rect.left() + 6, top + 6 + index * row_height,
                               rect.width() - 12, row_height)
            draw_state_box(painter, row, state, text, font_scale=0.9)
        return height
