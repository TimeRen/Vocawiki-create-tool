"""右下角的下载进度环（用户 2026-10-24 要求：「更新版本时在右下角显示一个进度环」）。

只做一件事：在窗口右下角浮一个圆环 + 百分比，`set_progress()` 更新、`finish()` 收起来。
它不参与布局（`place()` 按父窗口尺寸手工定位），所以不会挤动任何页面；也不吃鼠标事件。
"""
from __future__ import annotations

from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets

# 环的外径与粗细（dp 由调用方按字号缩放自己决定就行，这里用固定像素，和状态栏同一量级）
RING_SIZE = 78
RING_THICKNESS = 7
# 离窗口右下角多远（状态栏高度会另外加进去）
RING_MARGIN = 16
# 颜色：底、轨道、进度、文字。深色主题下 `theme.py` 会把它们换掉（见 `apply_theme()`）
BACKGROUND = QtGui.QColor(255, 255, 255, 235)
TRACK = QtGui.QColor(200, 200, 200, 180)
PROGRESS = QtGui.QColor(51, 102, 204)
TEXT = QtGui.QColor(32, 33, 34)


class ProgressRing(QtWidgets.QWidget):
    """一个圆环进度指示器：`set_progress(已下载, 总数)`；总数未知时画成「转圈」的样子。"""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._ratio = 0.0
        self._indeterminate = True
        self._phase = 0.0
        self._text = ""
        self.setFixedSize(RING_SIZE, RING_SIZE)
        # 只是显示：别挡住下面的按钮 / 别抢焦点
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._spin)
        self.hide()

    # —— 对外 ——
    def set_progress(self, done: int, total: int, text: str = "") -> None:
        """更新进度；`total <= 0` 时按「不知道总量」处理（只转圈、不显示百分比）。"""
        self._indeterminate = int(total or 0) <= 0
        self._ratio = 0.0 if self._indeterminate else max(0.0, min(1.0, done / total))
        self._text = text or self._text
        self.setToolTip(self._text)
        self.show()
        self.raise_()
        if self._indeterminate:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def finish(self) -> None:
        """下载结束（成功或失败）都收起来。"""
        self._timer.stop()
        self.hide()

    def set_text(self, text: str) -> None:
        self._text = text or ""
        self.setToolTip(self._text)
        self.update()

    def progress_ratio(self) -> float:
        """当前进度（0~1）；测试与状态栏都用它。"""
        return self._ratio

    def place(self) -> None:
        """挪到父窗口右下角（状态栏上方），窗口一改大小就调一次。"""
        parent = self.parentWidget()
        if parent is None:
            return
        bottom = RING_MARGIN
        status = getattr(parent, "statusBar", None)
        if callable(status):
            try:
                bottom += int(status().height() or 0)
            except Exception:                      # noqa: BLE001 - 没有状态栏就算了
                pass
        self.move(max(0, parent.width() - self.width() - RING_MARGIN),
                  max(0, parent.height() - self.height() - bottom))

    # —— 画 ——
    def _spin(self) -> None:
        self._phase = (self._phase + 12.0) % 360.0
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:      # noqa: N802 - Qt 约定
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        box = QtCore.QRectF(RING_THICKNESS / 2 + 1, RING_THICKNESS / 2 + 1,
                            self.width() - RING_THICKNESS - 2,
                            self.height() - RING_THICKNESS - 2)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(BACKGROUND)
        painter.drawEllipse(QtCore.QRectF(0, 0, self.width(), self.height()))
        track = QtGui.QPen(TRACK, RING_THICKNESS)
        track.setCapStyle(QtCore.Qt.RoundCap)
        painter.setPen(track)
        painter.drawArc(box, 0, 360 * 16)
        arc = QtGui.QPen(PROGRESS, RING_THICKNESS)
        arc.setCapStyle(QtCore.Qt.RoundCap)
        painter.setPen(arc)
        if self._indeterminate:
            # 不知道总量：一小段弧在转（和下载中常见的转圈一样）
            painter.drawArc(box, int(-self._phase * 16), int(-90 * 16))
        else:
            painter.drawArc(box, 90 * 16, int(-self._ratio * 360 * 16))
        font = self.font()
        font.setPointSizeF(max(8.0, font.pointSizeF() * 0.95))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(TEXT)
        label = "…" if self._indeterminate else f"{int(self._ratio * 100)}%"
        painter.drawText(self.rect(), QtCore.Qt.AlignCenter, label)
        painter.end()
