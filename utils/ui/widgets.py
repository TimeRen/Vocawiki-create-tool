"""样式 / 歌词 / 提交三个编辑器页共用的 Qt 小控件。

- `ColorField`：色板 + hex 输入 + 透明度 + 「透」 + 吸管（与原 html 版的 .cfield 一样是四件套）。
- `CoverView`：显示封面图，支持点击取色（取的是**原图**像素）。
- `CollapsibleBox`：可折叠分组（原界面右侧那一堆 group）。
- `SectionList`：右边那种「分段 + 次级 Tab」的两级切换条。
"""
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets

from utils.string import is_empty

CHECKER_PIXMAP: Optional[QtGui.QPixmap] = None


def checker_brush() -> QtGui.QBrush:
    """透明色的棋盘格底纹（与 html 版 .cf-tr 的观感一致）。"""
    global CHECKER_PIXMAP
    if CHECKER_PIXMAP is None:
        pixmap = QtGui.QPixmap(16, 16)
        pixmap.fill(QtGui.QColor("#ffffff"))
        painter = QtGui.QPainter(pixmap)
        painter.fillRect(0, 0, 8, 8, QtGui.QColor("#d8dbe6"))
        painter.fillRect(8, 8, 8, 8, QtGui.QColor("#d8dbe6"))
        painter.end()
        CHECKER_PIXMAP = pixmap
    return QtGui.QBrush(CHECKER_PIXMAP)


# ---------------------------------------------------------------- 颜色四件套

class ColorField(QtWidgets.QWidget):
    """一个颜色控件：色板、hex、透明度、透、吸管。"""

    changed = QtCore.pyqtSignal()
    pick_requested = QtCore.pyqtSignal(object)     # 参数是自身，供面板进入吸管模式

    def __init__(self, with_alpha: bool = True, parent=None):
        super().__init__(parent)
        self._color = "#ffffff"
        self._alpha = 1.0
        self._with_alpha = with_alpha
        self.with_picker = True

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.swatch = QtWidgets.QToolButton(self)
        self.swatch.setFixedSize(34, 24)
        self.swatch.setToolTip("点击选择颜色")
        self.swatch.clicked.connect(self._choose_color)
        row.addWidget(self.swatch)

        self.hex_edit = QtWidgets.QLineEdit(self)
        self.hex_edit.setMaximumWidth(88)
        self.hex_edit.setPlaceholderText("#rrggbb")
        self.hex_edit.editingFinished.connect(self._apply_hex)
        self.hex_edit.returnPressed.connect(self._apply_hex)
        row.addWidget(self.hex_edit)

        self.alpha_spin = QtWidgets.QSpinBox(self)
        self.alpha_spin.setRange(0, 100)
        self.alpha_spin.setSuffix("%")
        self.alpha_spin.setMaximumWidth(64)
        self.alpha_spin.valueChanged.connect(self._apply_alpha)
        row.addWidget(self.alpha_spin)
        self.alpha_spin.setVisible(with_alpha)

        self.transparent_button = QtWidgets.QToolButton(self)
        self.transparent_button.setText("透")
        self.transparent_button.setCheckable(True)
        self.transparent_button.setToolTip("整块透明（再点一次恢复）")
        self.transparent_button.clicked.connect(self._toggle_transparent)
        row.addWidget(self.transparent_button)

        self.pick_button = QtWidgets.QToolButton(self)
        self.pick_button.setText("吸管")
        self.pick_button.setCheckable(True)
        self.pick_button.setToolTip("在封面图上点击取色")
        self.pick_button.clicked.connect(lambda: self.pick_requested.emit(self))
        row.addWidget(self.pick_button)
        row.addStretch(1)

        self._last_solid = (self._color, 1.0)
        self.set_value(self._color, self._alpha)

    # —— 取值 / 赋值 ——
    def value(self) -> Tuple[str, float]:
        return self._color, self._alpha

    def set_value(self, color: str, alpha: float = 1.0, notify: bool = False) -> None:
        from utils.ui import style_state
        self._color = style_state.norm_hex(color) or "#ffffff"
        self._alpha = max(0.0, min(1.0, float(alpha)))
        if self._alpha > 0:
            self._last_solid = (self._color, self._alpha)
        self.hex_edit.setText(self._color)
        self.alpha_spin.blockSignals(True)
        self.alpha_spin.setValue(round(self._alpha * 100))
        self.alpha_spin.blockSignals(False)
        self._refresh_swatch()
        if notify:
            self.changed.emit()

    def set_pick_active(self, active: bool) -> None:
        self.pick_button.setChecked(active)

    def _refresh_swatch(self) -> None:
        if self._alpha <= 0:
            # 透明：棋盘格底纹 + 虚线边框（与原 html 版「透」按钮的观感一致）
            self.swatch.setStyleSheet(
                "QToolButton { border: 1px dashed #b9bfcf;"
                " background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #ffffff,"
                " stop:0.49 #ffffff, stop:0.5 #d8dbe6, stop:1 #d8dbe6); }")
            self.transparent_button.setChecked(True)
            return
        from utils.ui import style_state
        css = style_state.css_color(self._color, self._alpha)
        self.swatch.setStyleSheet(
            f"QToolButton {{ background: {css}; border: 1px solid #b9bfcf; }}")
        self.transparent_button.setChecked(False)

    # —— 交互 ——
    def _choose_color(self) -> None:
        initial = QtGui.QColor(self._color)
        options = (QtWidgets.QColorDialog.ShowAlphaChannel if self._with_alpha
                   else QtWidgets.QColorDialog.ColorDialogOptions())
        picked = QtWidgets.QColorDialog.getColor(initial, self, "选择颜色", options)
        if not picked.isValid():
            return
        alpha = picked.alphaF() if self._with_alpha else 1.0
        self.set_value(picked.name(), alpha)
        self.changed.emit()

    def _apply_hex(self) -> None:
        from utils.ui import style_state
        text = self.hex_edit.text().strip()
        if is_empty(text):
            self.set_value(self._color, self._alpha)
            return
        hexed = style_state.norm_hex(text)
        if hexed is None:
            self._flash_invalid()
            return
        self._color = hexed
        self.hex_edit.setText(hexed)
        self._refresh_swatch()
        self.changed.emit()

    def _apply_alpha(self, value: int) -> None:
        self._alpha = value / 100
        if self._alpha > 0:
            self._last_solid = (self._color, self._alpha)
        self._refresh_swatch()
        self.changed.emit()

    def _toggle_transparent(self) -> None:
        from utils.ui import style_state
        if self._alpha <= 0:
            color, alpha = self._last_solid
            self.set_value(color, alpha)
        else:
            self.set_value(self._color, 0.0)
        self._refresh_swatch()
        self.changed.emit()

    def _flash_invalid(self) -> None:
        """色值非法：红框提示一下再把内容还原。"""
        self.hex_edit.setStyleSheet("QLineEdit { border: 1px solid #b32424; }")
        QtCore.QTimer.singleShot(900, lambda: (
            self.hex_edit.setStyleSheet(""), self.set_value(self._color, self._alpha)))
        self.hex_edit.setToolTip("色值无效，已还原（支持 #RGB / #RRGGBB）")


# ---------------------------------------------------------------- 封面图

class CoverView(QtWidgets.QFrame):
    """显示封面图；取色模式下点一下就把该像素的颜色发出来（取原图像素）。

    它还兼作辅助工具的画布（测量 / 参照物，见 utils/ui/aux_tools.py）。
    """

    picked = QtCore.pyqtSignal(str)
    aux_changed = QtCore.pyqtSignal(str)          # 辅助工具状态变了（"ok" / "full"）
    aux_exit = QtCore.pyqtSignal()                # 右键要求退出工具模式
    MAX_HEIGHT = 250

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setMinimumHeight(120)
        self.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #c8ccd1; }")
        self._image: Optional[QtGui.QImage] = None
        self._pick = False
        self._rect = QtCore.QRect()
        self._aux = None
        self._aux_space = "cover"
        self.setMouseTracking(True)

    def set_aux(self, aux, space: str = "cover") -> None:
        """挂上辅助工具状态（共用一份，两边各记各的坐标）。"""
        self._aux = aux
        self._aux_space = space
        self.update()

    def has_image(self) -> bool:
        return self._image is not None and not self._image.isNull()

    def set_pick_mode(self, active: bool) -> None:
        self._pick = active
        self.setCursor(QtCore.Qt.CrossCursor if active else QtCore.Qt.ArrowCursor)
        self.update()

    def load_file(self, path) -> bool:
        if path is None:
            return False
        image = QtGui.QImage(str(path))
        if image.isNull():
            return False
        self._set_image(image)
        return True

    def load_data(self, data: bytes) -> bool:
        image = QtGui.QImage()
        if not image.loadFromData(QtCore.QByteArray(data)):
            return False
        self._set_image(image)
        return True

    def _set_image(self, image: QtGui.QImage) -> None:
        self._image = image.convertToFormat(QtGui.QImage.Format_RGB32)
        self.setMinimumHeight(min(self.MAX_HEIGHT, max(120, self._image.height())))
        self.updateGeometry()
        self.update()

    def clear_image(self) -> None:
        self._image = None
        self._pick = False
        self.setCursor(QtCore.Qt.ArrowCursor)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        if not self.has_image():
            painter.setPen(QtGui.QColor("#72777d"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter,
                             "没有封面图\n可在预览区导入图片后再到图上取色")
        else:
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            area = self.rect().adjusted(1, 1, -1, -1)
            scaled = self._image.size().scaled(area.size(), QtCore.Qt.KeepAspectRatio)
            target = QtCore.QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(area.center())
            self._rect = target
            painter.drawImage(target, self._image)
        self._paint_aux(painter)

    def _paint_aux(self, painter: QtGui.QPainter) -> None:
        if self._aux is not None:
            self._aux.paint(painter, self._aux_space)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._aux is not None and self._aux.mode:
            if event.button() == QtCore.Qt.RightButton:
                self.aux_exit.emit()
            elif event.button() == QtCore.Qt.LeftButton:
                self.aux_changed.emit(self._aux.click(self._aux_space,
                                                     (event.pos().x(), event.pos().y())))
            return
        if not (self._pick and self.has_image() and self._rect.contains(event.pos())):
            super().mousePressEvent(event)
            return
        # 屏幕坐标 → 原图像素坐标（取色不因缩放而失真）
        scale_x = self._image.width() / max(1, self._rect.width())
        scale_y = self._image.height() / max(1, self._rect.height())
        x = int((event.pos().x() - self._rect.x()) * scale_x)
        y = int((event.pos().y() - self._rect.y()) * scale_y)
        x = max(0, min(self._image.width() - 1, x))
        y = max(0, min(self._image.height() - 1, y))
        color = QtGui.QColor(self._image.pixel(x, y))
        self.picked.emit(color.name())


# ---------------------------------------------------------------- 折叠分组

class CollapsibleBox(QtWidgets.QWidget):
    """标题一行（可点开/收起）+ 内容区。"""

    def __init__(self, title: str, expanded: bool = False, parent=None):
        super().__init__(parent)
        self._toggle = QtWidgets.QToolButton(self)
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
        self._toggle.clicked.connect(self._on_toggled)
        self._toggle.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                   QtWidgets.QSizePolicy.Fixed)

        self.content = QtWidgets.QWidget(self)
        self.body = QtWidgets.QVBoxLayout(self.content)
        self.body.setContentsMargins(8, 4, 4, 8)
        self.body.setSpacing(6)
        self.content.setVisible(expanded)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._toggle)
        layout.addWidget(self.content)

    def _on_toggled(self) -> None:
        expanded = self._toggle.isChecked()
        self._toggle.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
        self.content.setVisible(expanded)

    def add(self, widget: QtWidgets.QWidget) -> None:
        self.body.addWidget(widget)

    def add_row(self, label: str, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget(self.content)
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        text = QtWidgets.QLabel(label, row)
        text.setMinimumWidth(64)
        layout.addWidget(text)
        layout.addWidget(widget, 1)
        self.body.addWidget(row)
        return row


# ---------------------------------------------------------------- 两级切换条

class SectionTabs(QtWidgets.QWidget):
    """分段（Songbox / Introduction / 歌词）上面一行，次要目标下面一行。"""

    section_changed = QtCore.pyqtSignal(str)
    target_changed = QtCore.pyqtSignal(object)

    def __init__(self, sections: Sequence[Tuple[str, str]], parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._section_group = QtWidgets.QButtonGroup(self)
        self._target_group = QtWidgets.QButtonGroup(self)
        self._section_buttons: dict = {}
        self._target_buttons: dict = {}
        self._section_row = QtWidgets.QHBoxLayout()
        self._section_row.setSpacing(4)
        layout.addLayout(self._section_row)
        self._target_row = QtWidgets.QHBoxLayout()
        self._target_row.setSpacing(4)
        layout.addLayout(self._target_row)
        for key, label in sections:
            button = QtWidgets.QToolButton(self)
            button.setText(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, name=key: self.section_changed.emit(name))
            self._section_group.addButton(button)
            self._section_row.addWidget(button)
            self._section_buttons[key] = button
        self._section_row.addStretch(1)
        self._target_row.addStretch(1)

    def set_section(self, key: str) -> None:
        for name, button in self._section_buttons.items():
            button.setChecked(name == key)

    def set_targets(self, targets: Sequence[Tuple[str, str]], current: Any) -> List[QtWidgets.QToolButton]:
        """重建次级 Tab；返回按钮列表。"""
        widgets = []
        while self._target_row.count():
            item = self._target_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        while self._target_group.buttons():
            self._target_group.removeButton(self._target_group.buttons()[0])
        self._target_buttons = {}
        for key, label in targets:
            button = QtWidgets.QToolButton(self)
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(key == current)
            button.clicked.connect(lambda _checked, name=key: self.target_changed.emit(name))
            self._target_group.addButton(button)
            self._target_row.addWidget(button)
            self._target_buttons[key] = button
            widgets.append(button)
        self._target_row.addStretch(1)
        return widgets

    def set_target(self, target: Any) -> None:
        for key, button in self._target_buttons.items():
            button.setChecked(key == target)
