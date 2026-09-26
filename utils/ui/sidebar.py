"""左侧竖栏：功能切换 + 头像（登录 Vocawiki）+ 设置齿轮。

Timeless 皮肤左侧就是个细长导航栏：浅灰底、右侧一条细边框、选中项白底 + 左边一条
强调色。这里照这个做，宽度固定 64px：

    ┌────┐
    │ V  │  品牌块（有程序图标就用程序图标）
    │功能│  小节标题
    │ 歌 │  生成歌曲条目（选中 = 白底 + 左侧蓝条）
    │    │
    │ .. │  ← 以后的功能加在这里
    │    │
    │ ◯  │  头像：未登录 = 站点默认头像；已登录 = 本人头像；点一下登录
    │ ⚙  │  设置（原来的左下角「设置」按钮搬到这儿）
    └────┘

侧栏只负责「选哪个功能」与发信号，具体做什么由主窗口接。
"""
from typing import Iterable, List, Optional, Sequence, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets

from utils.ui import avatar as avatar_lib
from utils.ui import icons, theme

WIDTH = 64
BUTTON = 44
AVATAR_SIZE = 40

# 功能列表：(key, 名称, 图标, 说明)。以后加功能就往这里加一项。
FEATURES: Sequence[Tuple[str, str, str, str]] = (
    ("entry", "生成歌曲条目", "song",
     "按曲名 / 视频链接生成 Vocawiki 条目（当前唯一的功能）"),
)


def _side_button(parent: QtWidgets.QWidget, name: str, tooltip: str,
                 checkable: bool = False) -> QtWidgets.QToolButton:
    button = QtWidgets.QToolButton(parent)
    button.setObjectName(name)
    button.setToolTip(tooltip)
    button.setCheckable(checkable)
    button.setAutoRaise(True)
    button.setFixedSize(BUTTON, BUTTON)
    button.setIconSize(QtCore.QSize(22, 22))
    button.setFocusPolicy(QtCore.Qt.TabFocus)
    button.setCursor(QtCore.Qt.PointingHandCursor)
    return button


class AvatarButton(QtWidgets.QToolButton):
    """圆形头像按钮：显示站点默认头像 / 本人头像，右下角一个小圆点表示登录状态。"""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setObjectName("avatarButton")
        self.setAutoRaise(True)
        self.setFixedSize(AVATAR_SIZE + 8, AVATAR_SIZE + 8)
        self.setIconSize(QtCore.QSize(AVATAR_SIZE, AVATAR_SIZE))
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.TabFocus)
        self._username = ""
        self._logged_in = False
        self._busy = False
        self.refresh()

    # —— 状态 ——
    @property
    def logged_in(self) -> bool:
        return self._logged_in

    def set_state(self, username: str = "", logged_in: bool = False,
                  image_bytes: Optional[bytes] = None) -> None:
        self._username = username or ""
        self._logged_in = bool(logged_in)
        self.refresh(image_bytes)

    def set_busy(self, busy: bool, note: str = "") -> None:
        """登录中：按钮先禁用，鼠标提示说明在干什么。"""
        self._busy = bool(busy)
        self.setEnabled(not self._busy)
        if self._busy:
            self.setToolTip(note or "正在登录 Vocawiki…")
        else:
            self.refresh()

    def refresh(self, image_bytes: Optional[bytes] = None) -> None:
        pixmap = avatar_lib.avatar_pixmap(self._username, AVATAR_SIZE,
                                         self._logged_in, image_bytes)
        ratio = avatar_lib.dpr()
        self.setIcon(QtGui.QIcon(_with_dot(pixmap, self._logged_in, ratio)))
        if self._busy:
            return
        if self._logged_in:
            who = self._username or "Vocawiki"
            self.setToolTip(f"已登录 Vocawiki：{who}\n点击可重新登录 / 退出登录")
        else:
            self.setToolTip("未登录 Vocawiki（显示站点默认头像）\n点击登录")


def _with_dot(pixmap: QtGui.QPixmap, logged_in: bool, ratio: float) -> QtGui.QPixmap:
    """右下角小圆点：登录了 = 绿点，没登录 = 灰点（带白边，压在头像上）。

    整个函数都按设备像素画（pixmap 是带 DPR 的），最后给结果设回 DPR。
    """
    result = QtGui.QPixmap(pixmap.size())
    result.fill(QtCore.Qt.transparent)
    result.setDevicePixelRatio(ratio)
    painter = QtGui.QPainter(result)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    painter.drawPixmap(0, 0, avatar_lib.plain_pixmap(pixmap))
    size = float(pixmap.width())
    radius = 5.0 * ratio
    center = QtCore.QPointF(size - radius - 2.0 * ratio, size - radius - 2.0 * ratio)
    painter.setPen(QtGui.QPen(QtGui.QColor(theme.BG_PAGE), 2.0 * ratio))
    painter.setBrush(QtGui.QBrush(QtGui.QColor(
        theme.SUCCESS if logged_in else theme.TEXT_MUTED)))
    painter.drawEllipse(center, radius, radius)
    painter.end()
    return result


class SideBar(QtWidgets.QWidget):
    """左竖栏。信号：feature_selected(key) / settings_requested / avatar_clicked。"""

    feature_selected = QtCore.pyqtSignal(str)
    settings_requested = QtCore.pyqtSignal()
    avatar_clicked = QtCore.pyqtSignal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None,
                 features: Iterable[Tuple[str, str, str, str]] = FEATURES,
                 brand: Optional[QtGui.QPixmap] = None):
        super().__init__(parent)
        self.setObjectName("sideBar")
        self.setFixedWidth(WIDTH)
        self._buttons: List[QtWidgets.QToolButton] = []
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(2)

        self.brand = QtWidgets.QLabel(self)
        self.brand.setObjectName("sideBrand")
        self.brand.setAlignment(QtCore.Qt.AlignCenter)
        self.brand.setPixmap(brand if brand is not None else icons.brand_pixmap(30))
        self.brand.setToolTip("Vocawiki 条目辅助工具")
        layout.addWidget(self.brand, 0, QtCore.Qt.AlignHCenter)
        layout.addSpacing(10)

        caption = QtWidgets.QLabel("功能", self)
        caption.setObjectName("sideCaption")
        caption.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(caption)
        layout.addSpacing(4)

        self.features_box = QtWidgets.QVBoxLayout()
        self.features_box.setContentsMargins(0, 0, 0, 0)
        self.features_box.setSpacing(2)
        layout.addLayout(self.features_box)
        for key, label, icon_name, tip in features:
            self.add_feature(key, label, icon_name, tip)
        if self._buttons:                      # 竖栏总得有一个选中的功能
            self._buttons[0].setChecked(True)
            self._recolor()

        layout.addStretch(1)

        separator = QtWidgets.QFrame(self)
        separator.setObjectName("sideSeparator")
        separator.setFrameShape(QtWidgets.QFrame.HLine)
        separator.setFixedHeight(1)
        layout.addWidget(separator, 0)
        layout.addSpacing(8)

        self.avatar_button = AvatarButton(self)
        self.avatar_button.clicked.connect(self.avatar_clicked.emit)
        layout.addWidget(self.avatar_button, 0, QtCore.Qt.AlignHCenter)
        layout.addSpacing(6)

        self.settings_button = _side_button(self, "sideButton",
                                           "设置：可视化修改 config.yaml 与 "
                                           "wiki_credentials.yaml（账号 / AI 密钥）")
        self.settings_button.setIcon(icons.icon("gear", 22, theme.TEXT_QUIET))
        self.settings_button.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self.settings_button, 0, QtCore.Qt.AlignHCenter)

        self._apply_style()

    # —— 功能项 ——
    def add_feature(self, key: str, label: str, icon_name: str, tooltip: str = "") -> None:
        button = _side_button(self, "sideFeature",
                              f"{label}\n{tooltip}" if tooltip else label, checkable=True)
        button.setProperty("featureKey", key)
        button.setProperty("iconName", icon_name)
        button.setIcon(icons.icon(icon_name, 22, theme.TEXT_QUIET))
        button.clicked.connect(lambda: self.feature_selected.emit(key))
        self._group.addButton(button)
        self.features_box.addWidget(button, 0, QtCore.Qt.AlignHCenter)
        self._buttons.append(button)
        self._recolor()

    def keys(self) -> List[str]:
        return [button.property("featureKey") for button in self._buttons]

    def label_for(self, key: str) -> str:
        """功能名（鼠标提示的第一行）。"""
        for button in self._buttons:
            if button.property("featureKey") == key:
                return button.toolTip().splitlines()[0]
        return key

    def current_feature(self) -> str:
        for button in self._buttons:
            if button.isChecked():
                return str(button.property("featureKey"))
        return ""

    def set_current_feature(self, key: str) -> None:
        for button in self._buttons:
            if button.property("featureKey") == key:
                button.setChecked(True)
                self._recolor()
                return

    def _recolor(self) -> None:
        """选中的功能图标用强调色，其余用灰（QSS 管不到图标颜色）。"""
        for button in self._buttons:
            color = theme.ACCENT if button.isChecked() else theme.TEXT_QUIET
            name = button.property("iconName") or "list"
            button.setIcon(icons.icon(name, 22, color))

    # —— 头像 ——
    def set_avatar(self, username: str = "", logged_in: bool = False,
                   image_bytes: Optional[bytes] = None) -> None:
        self.avatar_button.set_state(username, logged_in, image_bytes)

    def set_avatar_busy(self, busy: bool, note: str = "") -> None:
        self.avatar_button.set_busy(busy, note)

    # —— 外观 ——
    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
            QWidget#sideBar {{ background: {theme.BG_PAGE};
                               border-right: 1px solid {theme.BORDER}; }}
            QLabel#sideBrand {{ background: transparent; border: none; }}
            QLabel#sideCaption {{ background: transparent; border: none;
                                  color: {theme.TEXT_MUTED}; font-size: 10px;
                                  letter-spacing: 2px; }}
            QFrame#sideSeparator {{ background: {theme.BORDER}; border: none; }}
            QToolButton#sideFeature, QToolButton#sideButton {{
                background: transparent; border: none; border-radius: 3px;
                margin-left: 5px; margin-right: 5px;
            }}
            QToolButton#sideFeature:hover, QToolButton#sideButton:hover {{
                background: {theme.BG_SUBTLE};
            }}
            QToolButton#sideFeature:checked {{
                background: {theme.BG}; border-left: 3px solid {theme.ACCENT};
                margin-left: 3px; margin-right: 5px;
            }}
            QToolButton#avatarButton {{
                background: transparent; border: none; border-radius: 20px;
            }}
            QToolButton#avatarButton:hover {{ background: {theme.BG_SUBTLE}; }}
        """)


__all__ = ["SideBar", "AvatarButton", "FEATURES", "WIDTH", "AVATAR_SIZE"]
