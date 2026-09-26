"""样式页的辅助工具：测量（两点距离）与参照物（线段 / 矩形）。

对应原 `html/css-tag-editor.html` 里的 `#aux-group`：

- **测量**：在画布上点两下，画出连线并给出距离与 Δx / Δy（`MEAS_COL`）。
- **参照物**：按当前「线型 / 角度 / 宽 / 高」的设置，在点击处放一个参照物（`AUX_COL`），最多 24 个。

坐标一律是**控件局部像素**（与 html 版「以 stage 局部屏幕像素计」一致，不做缩放换算）。
画布有两块：左边预览台（`style_preview.StylePreview`）与封面图（`widgets.CoverView`），
两块各记各的数据（每个对象带 `space`），所以互不干扰 —— 状态与绘制都在这里，控件只负责把点击坐标交进来。
"""
from typing import Any, Dict, List, Optional, Tuple

from PyQt5 import QtCore, QtGui

AUX_COLOR = "#ff5a7a"                     # 参照物
MEASURE_COLOR = "#2f80ff"                 # 测量
MAX_GUIDES = 24
GUIDE_KINDS = ("line", "rect")
GUIDE_KIND_LABELS = {"line": "线段", "rect": "矩形"}
SPACE_NAMES = {"preview": "预览台", "cover": "封面"}
SPACES = tuple(SPACE_NAMES)

# 新增参照物的默认值（与 html 版 guide 默认 {kind:'line', angle:0, w:300, h:120} 一致）
DEFAULT_GUIDE = {"kind": "line", "angle": 0, "w": 300, "h": 120}

CLICK_OK = "ok"
CLICK_FULL = "full"                       # 参照物到上限了
CLICK_IDLE = "idle"                       # 没在工具模式里，这次点击不算


class AuxState:
    """测量点 + 参照物 + 当前模式（与具体控件无关）。"""

    def __init__(self) -> None:
        self.mode: Optional[str] = None               # None / "measure" / "guide"
        self.points: Dict[str, List[Tuple[float, float]]] = {space: [] for space in SPACES}
        self.guides: List[Dict[str, Any]] = []
        self.settings: Dict[str, Any] = dict(DEFAULT_GUIDE)
        self.last_space: str = "preview"

    # ------------------------------------------------------------ 模式与数据

    def set_mode(self, mode: Optional[str]) -> None:
        """切换工具模式；进入「测量」时清掉上一次的测点。"""
        self.mode = mode
        if mode == "measure":
            self.clear_measure()

    def clear_measure(self) -> None:
        for space in SPACES:
            self.points[space] = []

    def clear_guides(self) -> None:
        self.guides = []

    def click(self, space: str, point: Tuple[float, float]) -> str:
        """在某个画布上点了一下；返回 CLICK_OK / CLICK_FULL / CLICK_IDLE。"""
        self.last_space = space if space in SPACES else "preview"
        x, y = float(point[0]), float(point[1])
        if self.mode == "measure":
            marks = self.points[self.last_space]
            if len(marks) >= 2:
                marks.clear()             # 已有一次完整测量：这一下当作新的起点
            marks.append((x, y))
            return CLICK_OK
        if self.mode == "guide":
            if len(self.guides) >= MAX_GUIDES:
                return CLICK_FULL
            self.guides.append({**self.settings, "space": self.last_space, "x": x, "y": y})
            return CLICK_OK
        return CLICK_IDLE

    def measure_rows(self, space: Optional[str] = None) -> List[str]:
        """测量结果的文字（界面直接显示）。"""
        space = space or self.last_space
        marks = self.points.get(space) or []
        if not marks:
            return []
        if len(marks) == 1:
            return [f"起点 ({marks[0][0]:.0f}, {marks[0][1]:.0f})，再点一下量距离"]
        (x1, y1), (x2, y2) = marks[0], marks[1]
        dx, dy = x2 - x1, y2 - y1
        distance = (dx ** 2 + dy ** 2) ** 0.5
        return [f"距离 {distance:.0f}px（Δx {dx:+.0f} / Δy {dy:+.0f}）",
                f"({x1:.0f}, {y1:.0f}) → ({x2:.0f}, {y2:.0f})"]

    def measure_text(self, space: Optional[str] = None) -> str:
        space = space or self.last_space
        rows = self.measure_rows(space)
        if not rows:
            return "在预览台或封面上点两下量距离"
        return f"{SPACE_NAMES.get(space, space)}：" + "；".join(rows)

    def guide_text(self) -> str:
        kinds = {}
        for guide in self.guides:
            kinds[guide.get("kind", "line")] = kinds.get(guide.get("kind", "line"), 0) + 1
        detail = "、".join(f"{GUIDE_KIND_LABELS.get(key, key)} {count} 个"
                           for key, count in kinds.items())
        text = f"共 {len(self.guides)} 个参照物（最多 {MAX_GUIDES} 个）"
        return f"{text}：{detail}" if detail else text

    # ------------------------------------------------------------ 绘制

    def paint(self, painter: QtGui.QPainter, space: str) -> None:
        """把这块画布上的参照物与测量画出来（坐标就是控件局部像素）。"""
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self._paint_guides(painter, space)
        self._paint_measure(painter, space)
        painter.restore()

    def _paint_guides(self, painter: QtGui.QPainter, space: str) -> None:
        pen = QtGui.QPen(QtGui.QColor(AUX_COLOR))
        pen.setWidth(1)
        pen.setStyle(QtCore.Qt.DashLine)
        for guide in self.guides:
            if guide.get("space") != space:
                continue
            x, y = float(guide.get("x", 0)), float(guide.get("y", 0))
            width = max(1.0, float(guide.get("w", 0)))
            height = max(1.0, float(guide.get("h", 0)))
            angle = float(guide.get("angle", 0))
            painter.save()
            painter.translate(QtCore.QPointF(x, y))
            painter.rotate(angle)
            painter.setPen(pen)
            if guide.get("kind") == "rect":
                painter.drawRect(QtCore.QRectF(-width / 2, -height / 2, width, height))
            else:
                painter.drawLine(QtCore.QPointF(-width / 2, 0), QtCore.QPointF(width / 2, 0))
            painter.restore()
            # 锚点：无论怎么旋转都画在原处，方便看出参照物挂在哪
            painter.setPen(QtGui.QPen(QtGui.QColor(AUX_COLOR), 1))
            painter.setBrush(QtGui.QColor(AUX_COLOR))
            painter.drawEllipse(QtCore.QPointF(x, y), 2.5, 2.5)
            painter.setBrush(QtCore.Qt.NoBrush)

    def _paint_measure(self, painter: QtGui.QPainter, space: str) -> None:
        marks = self.points.get(space) or []
        if not marks:
            return
        pen = QtGui.QPen(QtGui.QColor(MEASURE_COLOR), 1)
        painter.setPen(pen)
        if len(marks) == 1:
            painter.drawEllipse(QtCore.QPointF(*marks[0]), 3.0, 3.0)
            return
        (x1, y1), (x2, y2) = marks[0], marks[1]
        pen.setStyle(QtCore.Qt.SolidLine)
        painter.setPen(pen)
        painter.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))
        for point in (marks[0], marks[1]):
            painter.drawEllipse(QtCore.QPointF(*point), 3.0, 3.0)
        rows = self.measure_rows(space)
        label = rows[0] if rows else ""
        if label:
            middle = QtCore.QPointF((x1 + x2) / 2, (y1 + y2) / 2 - 6)
            painter.setPen(QtGui.QPen(QtGui.QColor(MEASURE_COLOR)))
            painter.drawText(middle, label)
