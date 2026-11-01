"""「这几张截图哪张当封面」的选择框（用户 2026-11-02 要求）。

只截一张时，那一张可能正好是黑屏 / 过渡帧 / 满屏大字、根本不适合当封面。所以封面这条路
改成「一次拍几张候选（页面声明的封面原图、页面上的封面元素、播放器画面的 35% / 70%），
再弹这个框让用户挑」。流程在后台线程跑，对话框由主线程弹（见 `window.ask_cover()`）。

没有图形界面（终端模式）时调用方不会走到这里，仍按老规则自动挑（见 `utils.image`）。
"""
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets

from utils.ui import theme

THUMB_SIZE = 220          # 缩略图边长（正方形按比例缩放）


def _thumbnail(path: Path) -> QtGui.QPixmap:
    """把候选图缩成缩略图（读不出来时给一个空图，别让对话框崩掉）。"""
    pixmap = QtGui.QPixmap(str(path))
    if pixmap.isNull():
        return pixmap
    return pixmap.scaled(THUMB_SIZE, THUMB_SIZE, QtCore.Qt.KeepAspectRatio,
                         QtCore.Qt.SmoothTransformation)


class CoverPickerDialog(QtWidgets.QDialog):
    """候选封面的缩略图网格：选中一张 → 「用这一张」；或「都不合适」。"""

    def __init__(self, candidates: Sequence[Tuple[Path, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择封面")
        self.setModal(True)
        self._chosen: Optional[Path] = None
        self._candidates = [(Path(path), str(label)) for path, label in candidates]
        self._build_ui()

    # ------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        hint = QtWidgets.QLabel(
            "这几张都是从稿件页上取下来的候选封面，挑一张最合适的：", self)
        root.addWidget(hint)

        self.list = QtWidgets.QListWidget(self)
        self.list.setViewMode(QtWidgets.QListView.IconMode)
        self.list.setIconSize(QtCore.QSize(THUMB_SIZE, THUMB_SIZE))
        self.list.setResizeMode(QtWidgets.QListView.Adjust)
        self.list.setMovement(QtWidgets.QListView.Static)
        self.list.setSpacing(8)
        self.list.setWordWrap(True)
        self.list.setMinimumHeight(THUMB_SIZE + 60)
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        for index, (path, label) in enumerate(self._candidates):
            item = QtWidgets.QListWidgetItem(QtGui.QIcon(_thumbnail(path)),
                                             f"{index + 1}. {label}")
            item.setData(QtCore.Qt.UserRole, str(path))
            item.setToolTip(f"{label}\n{path}")
            self.list.addItem(item)
        if self._candidates:
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(
            lambda item: self._pick(Path(str(item.data(QtCore.Qt.UserRole)))))
        root.addWidget(self.list, 1)

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        self.none_button = QtWidgets.QPushButton("都不合适（保持原图）", self)
        self.none_button.clicked.connect(self.reject)
        footer.addWidget(self.none_button)
        self.ok_button = QtWidgets.QPushButton("用这一张", self)
        self.ok_button.setDefault(True)
        theme.mark_accent(self.ok_button)
        self.ok_button.clicked.connect(self._on_ok)
        footer.addWidget(self.ok_button)
        root.addLayout(footer)

    # ------------------------------------------------------------ 结果
    def selected(self) -> Optional[Path]:
        return self._chosen

    def _on_ok(self) -> None:
        item = self.list.currentItem()
        if item is None:
            self.reject()
            return
        self._pick(Path(str(item.data(QtCore.Qt.UserRole))))

    def _pick(self, path: Path) -> None:
        self._chosen = Path(path)
        self.accept()


def choose_cover(candidates: Sequence[Tuple[Path, str]], parent=None) -> Optional[Path]:
    """弹框让用户挑一张；用户点「都不合适」/ 关闭窗口时返回 None。

    `candidates` 是 `[(图片路径, 说明), …]`；只给一张时也走这里（用户可以否掉那一张）。
    """
    if not candidates:
        return None
    dialog = CoverPickerDialog(candidates, parent)
    if dialog.exec_() != QtWidgets.QDialog.Accepted:
        return None
    return dialog.selected()
