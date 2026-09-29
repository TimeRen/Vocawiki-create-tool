"""右下角浮出来的通知（一条一条地冒，几秒后自己收回去）。

用户 2026-09-29 要求：提交页的通知（提交结果、同步大家族模板的每一步、修正链入的每一页…）
除了写在状态行里，还要在**右下角**一条一条地弹出来（最初做在左下角，用户当天改成右下角）。

用法：
    self.toaster = Toaster(host_widget)         # host 的右下角就是通知出现的位置
    self.toaster.show_message("已提交「涅槃(HotaRu)」", "ok")

队列串行：上一条消失（自己超时、或被点掉）之后才冒下一条，所以不会一坨糊在一起。
卡片是 host 的子控件（不是独立窗口），跟着 host 的尺寸走，窗口缩放时会自动挪位置。
"""
from typing import List, Optional, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets

MARGIN = 12                    # 距 host 右边 / 下边的间距
SECONDS = 6.0                  # 一条通知显示多久
BUSY_SECONDS = 2.5             # 排队超过几条时缩短，免得攒着看不完
BUSY_QUEUE = 3
MAX_WIDTH = 460                # 卡片最大宽度（长了换行）
_PADDING = (10, 7, 12, 7)      # 左 / 上 / 右 / 下
_BORDER = 1

# 左侧色条的颜色，与状态行的配色一致（`submit_panel.set_status`）
_COLORS = {
    "ok": "#14866d",
    "err": "#b32424",
    "warn": "#ac6600",
    "info": "#3366cc",
}


class Toast(QtWidgets.QFrame):
    """一条通知卡片：左侧一条彩色竖线 + 文字，点一下立刻收回去。"""

    dismissed = QtCore.pyqtSignal()

    def __init__(self, text: str, kind: str = "info", seconds: float = SECONDS,
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("点一下收起这条通知")
        color = _COLORS.get(kind, _COLORS["info"])
        self.setStyleSheet(
            "QFrame#toast { background: #ffffff; border: 1px solid #c8ccd1;"
            f" border-left: 4px solid {color}; border-radius: 3px; }}")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(*_PADDING)
        layout.setSpacing(0)
        self.label = QtWidgets.QLabel(str(text), self)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
        layout.addWidget(self.label)
        self.setMaximumWidth(MAX_WIDTH)
        self._fit()
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._timer.start(int(max(1.0, float(seconds)) * 1000))
        self._effect = QtWidgets.QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QtCore.QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(160)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)

    def _fit(self) -> None:
        """把卡片高度算对。

        ⚠️ 光靠 `adjustSize()` 会把换行后的文字切掉：`QLabel` 开了 wordWrap 之后
        `sizeHint()` 给的高度是按「不换行」算的，得先用 `heightForWidth(宽度)` 问一次。
        """
        metrics = QtGui.QFontMetrics(self.label.font())
        budget = MAX_WIDTH - sum(_PADDING[::2]) - 2 * _BORDER          # 左右边距 + 左右边框
        if metrics.horizontalAdvance(self.label.text()) > budget:
            self.label.setFixedWidth(budget)
            needed = self.label.heightForWidth(budget)
            if needed > 0:
                self.label.setMinimumHeight(needed)
        self.adjustSize()

    def show_at(self, x: int, y: int) -> None:
        """摆到指定位置并淡入。"""
        self.adjustSize()
        self.move(int(x), int(y))
        self.show()
        self.raise_()
        self._fade.start()

    def dismiss(self) -> None:
        """收回去（超时或被点掉），告诉管理者可以冒下一条了。"""
        self._timer.stop()
        self.hide()
        self.dismissed.emit()
        self.deleteLater()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:      # noqa: N802 - Qt 约定
        if event.button() == QtCore.Qt.LeftButton:
            self.dismiss()
            return
        super().mouseReleaseEvent(event)


class Toaster(QtCore.QObject):
    """一串通知的管理者：排队、串行显示、跟着 host 挪位置。"""

    def __init__(self, host: QtWidgets.QWidget, seconds: float = SECONDS, parent=None):
        super().__init__(parent if parent is not None else host)
        self._host = host
        self._seconds = float(seconds)
        self._queue: List[Tuple[str, str]] = []
        self._current: Optional[Toast] = None
        host.installEventFilter(self)

    # —— 对外 ——

    def show_message(self, text: str, kind: str = "info") -> None:
        """排一条通知（空字符串直接丢掉）。"""
        text = str(text or "").strip()
        if not text:
            return
        self._queue.append((text, kind))
        if self._current is None:
            self._show_next()

    @property
    def pending(self) -> int:
        """还排着几条（含正在显示的那一条时可加 1）。"""
        return len(self._queue) + (1 if self._current is not None else 0)

    def clear(self) -> None:
        """清掉排队中的通知（正在显示的那条不动）。"""
        self._queue.clear()

    # —— 内部 ——

    def _show_next(self) -> None:
        if not self._queue:
            self._current = None
            return
        text, kind = self._queue.pop(0)
        seconds = self._seconds if len(self._queue) < BUSY_QUEUE else BUSY_SECONDS
        toast = Toast(text, kind, seconds, self._host)
        toast.dismissed.connect(self._show_next)
        self._current = toast
        self._place(toast)
        toast.show_at(*self._corner(toast))

    def _corner(self, toast: Toast) -> Tuple[int, int]:
        """host 右下角（留出边距），让整张卡片都在 host 里面。"""
        width = min(toast.sizeHint().width(), MAX_WIDTH)
        height = toast.sizeHint().height()
        x = max(MARGIN, self._host.width() - width - MARGIN)
        y = max(MARGIN, self._host.height() - height - MARGIN)
        return x, y

    def _place(self, toast: Toast) -> None:
        toast.move(*self._corner(toast))

    def eventFilter(self, obj, event) -> bool:          # noqa: N802 - Qt 约定
        """host 改变大小时把当前那条通知重新贴到右下角。"""
        if obj is self._host and event.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Show) \
                and self._current is not None:
            self._place(self._current)
        return False
