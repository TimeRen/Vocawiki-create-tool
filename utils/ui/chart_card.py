# -*- coding: utf-8 -*-
"""「榜单」页的一张卡片（一个名次一块）。

用户 2026-11-17 的要求（截图）：不要再是一张横向几十列的表格，而是**每首一块**：

     ┃ 32   曲名（粗体）            ┃
     ┃ PICKUP  P主 … 时间 … 编号   ┃
     ┃         得点 再生 评论 收藏 点赞 翻唱 ┃
     ┃         其他参数 · 备注      ┃

左边那条竖色条与角标名决定颜色（`chart_wikitext.BADGES`），名次用大字。
每一格都是紧凑的 `QLineEdit`，**左边配一个灰色小标签**（填了值以后也还看得出这一格
是「得点」还是「再生」），改完（`editingFinished`）就把值回写给 `ChartRow`。
"""
from typing import Dict, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from utils import chart_recognize, chart_wikitext
from utils.ui import theme

# 卡片里能改的字段：`(属性名, 小标签, 占宽比例)`
FIELD_ROWS = (
    (("title", "曲名", 3), ("entry", "条目", 2), ("vocalist", "歌姬", 2)),
    (("producer", "P主", 3), ("time", "时间", 2), ("video_id", "编号", 2)),
    (("score", "得点", 1), ("view", "再生", 1), ("comment", "评论", 1),
     ("mylist", "收藏", 1), ("like", "点赞", 1), ("cover", "翻唱", 1)),
)
BADGE_CHOICES = ("（无）",) + tuple(chart_wikitext.BADGE_NAMES)
SECTION_CHOICES = (("主榜", chart_recognize.SECTION_MAIN),
                   ("番外", chart_recognize.SECTION_EXTRA))
CARD_BACKGROUND = "#ffffff"
CARD_BORDER = "#c8ccd1"
SELECTED_BORDER = "#36c"
CAPTION_WIDTH = 30
CAPTION_STYLE = "QLabel { color: #72777d; font-size: 11px; }"


def badge_color(badge: str) -> str:
    """角标名 → 色条颜色（没有角标时返回空串，卡片就不画色条）。"""
    entry = chart_wikitext.BADGES.get(str(badge or "").strip())
    return entry[0] if entry else ""


class ChartCard(QtWidgets.QFrame):
    """一个名次一块。`changed` 在任一格改完时发；`picked` 在卡片被点中时发。"""

    changed = QtCore.pyqtSignal()
    picked = QtCore.pyqtSignal(object, bool)          # (自己, 是否按住了 Ctrl)

    def __init__(self, index: int, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.index = index
        self.row = None
        self._loading = False
        self._selected = False
        self.setObjectName("chartCard")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setAutoFillBackground(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setStyleSheet(
            f"#chartCard {{ background: {CARD_BACKGROUND}; border: 1px solid {CARD_BORDER};"
            f" border-radius: 6px; }}")
        self._build()

    # ------------------------------------------------------------ 界面
    def _build(self) -> None:
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 8, 0)
        row.setSpacing(8)

        self.strip = QtWidgets.QFrame(self)
        self.strip.setFixedWidth(4)
        self.strip.setStyleSheet("background: transparent;")
        row.addWidget(self.strip)

        left = QtWidgets.QVBoxLayout()
        left.setContentsMargins(6, 6, 0, 6)
        left.setSpacing(2)
        self.rank_edit = QtWidgets.QLineEdit("", self)
        self.rank_edit.setPlaceholderText("名次")
        self.rank_edit.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        rank_font = self.rank_edit.font()
        rank_font.setPointSize(max(13, int(rank_font.pointSize() * 1.8)))
        rank_font.setBold(True)
        self.rank_edit.setFont(rank_font)
        self.rank_edit.setFixedWidth(76)
        self.rank_edit.setStyleSheet(
            "QLineEdit { border: none; background: transparent; }"
            "QLineEdit:focus { border-bottom: 1px solid #36c; }")
        self.rank_edit.editingFinished.connect(self._on_edited)
        self.rank_edit.textEdited.connect(lambda _text: self.picked.emit(self, False))
        left.addWidget(self.rank_edit)
        self.badge_label = QtWidgets.QLabel("", self)
        badge_font = self.badge_label.font()
        badge_font.setPointSize(max(7, int(badge_font.pointSize() * 0.85)))
        badge_font.setBold(True)
        self.badge_label.setFont(badge_font)
        self.badge_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        left.addWidget(self.badge_label)
        left.addStretch(1)
        row.addLayout(left)

        right = QtWidgets.QVBoxLayout()
        right.setContentsMargins(0, 6, 0, 6)
        right.setSpacing(3)
        self.edits: Dict[str, QtWidgets.QLineEdit] = {}
        for fields in FIELD_ROWS:
            line = QtWidgets.QHBoxLayout()
            line.setSpacing(6)
            for name, label, stretch in fields:
                caption = QtWidgets.QLabel(label, self)
                caption.setStyleSheet(CAPTION_STYLE)
                caption.setFixedWidth(CAPTION_WIDTH)
                line.addWidget(caption)
                edit = QtWidgets.QLineEdit(self)
                edit.setToolTip(label)
                edit.setMinimumWidth(60 if name not in ("title", "entry", "time") else 140)
                edit.setStyleSheet(
                    "QLineEdit { border: none; border-bottom: 1px dotted transparent;"
                    f" background: transparent; padding: 1px 2px; }}"
                    "QLineEdit:hover { border-bottom: 1px dotted #a2a9b1; }"
                    "QLineEdit:focus { border-bottom: 1px solid #36c; }")
                if name == "title":
                    title_font = edit.font()
                    title_font.setBold(True)
                    edit.setFont(title_font)
                line.addWidget(edit, stretch)
                edit.editingFinished.connect(self._on_edited)
                self.edits[name] = edit
            right.addLayout(line)

        last = QtWidgets.QHBoxLayout()
        last.setSpacing(6)
        other_caption = QtWidgets.QLabel("其他", self)
        other_caption.setStyleSheet(CAPTION_STYLE)
        other_caption.setFixedWidth(CAPTION_WIDTH)
        other_caption.setToolTip("其他参数：写进模板的额外参数，用分号隔开")
        last.addWidget(other_caption)
        self.other_edit = QtWidgets.QLineEdit(self)
        self.other_edit.setPlaceholderText("其他参数（翻唱 = 1; 评论权重 = 0.97）")
        self.other_edit.setToolTip("写进模板的额外参数，用分号隔开")
        self.other_edit.setStyleSheet(self.edits["title"].styleSheet())
        self.other_edit.editingFinished.connect(self._on_edited)
        last.addWidget(self.other_edit, 3)
        self.section_box = QtWidgets.QComboBox(self)
        for label, _value in SECTION_CHOICES:
            self.section_box.addItem(label)
        self.section_box.setToolTip("小节：番外会单独写一节「=== UTAU番外榜 ===」")
        self.section_box.currentTextChanged.connect(self._on_section_changed)
        last.addWidget(self.section_box)
        self.badge_box = QtWidgets.QComboBox(self)
        self.badge_box.addItems(list(BADGE_CHOICES))
        self.badge_box.setToolTip("角标：PICKUP / TOPIC / HISTORY / OP / ED")
        self.badge_box.currentTextChanged.connect(self._on_badge_changed)
        last.addWidget(self.badge_box)
        right.addLayout(last)

        self.note_label = QtWidgets.QLabel("", self)
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet(theme.quiet_label_style())
        self.note_label.hide()
        right.addWidget(self.note_label)
        row.addLayout(right, 1)

    # ------------------------------------------------------------ 数据
    def load(self, row) -> None:
        """把 `ChartRow` 灌进卡片（不触发回写）。"""
        self.row = row
        self._loading = True
        try:
            for name, edit in self.edits.items():
                edit.setText(str(getattr(row, name, "") or ""))
            self.other_edit.setText(row.other_text())
            self.rank_edit.setText(str(row.rank or ""))
            section_label = dict((value, label) for label, value in SECTION_CHOICES)
            self.section_box.setCurrentText(
                section_label.get(str(row.section or "").strip(), SECTION_CHOICES[0][0]))
            badge = str(row.badge or "").strip()
            self.badge_box.setCurrentText(badge if badge in BADGE_CHOICES else BADGE_CHOICES[0])
            note = str(row.note or "").strip()
            self.note_label.setText(note)
            self.note_label.setVisible(bool(note))
            self.setToolTip(note)
            self._apply_badge_look(badge)
        finally:
            self._loading = False

    def _apply_badge_look(self, badge: str) -> None:
        color = badge_color(badge)
        self.strip.setStyleSheet(f"background: {color};" if color else "background: transparent;")
        name = str(badge or "").strip()
        self.badge_label.setText(name if name else "")
        self.badge_label.setStyleSheet(f"color: {color};" if color else "color: transparent;")
        if name:
            self.setStyleSheet(
                f"#chartCard {{ background: {CARD_BACKGROUND};"
                f" border: 1px solid {'#36c' if self._selected else CARD_BORDER};"
                " border-radius: 6px; }}")

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        border = SELECTED_BORDER if self._selected else CARD_BORDER
        self.setStyleSheet(
            f"#chartCard {{ background: {CARD_BACKGROUND}; border: 1px solid {border};"
            " border-radius: 6px; }")

    def field(self, name: str) -> QtWidgets.QLineEdit:
        """取某一格的输入框（测试与面板都用它，别再按下标猜）。"""
        if name == "other":
            return self.other_edit
        return self.edits[name]

    # ------------------------------------------------------------ 交互
    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:   # noqa: N802 - Qt 接口
        self.picked.emit(self, bool(event.modifiers() & QtCore.Qt.ControlModifier))
        super().mousePressEvent(event)

    def _on_edited(self) -> None:
        if self._loading or self.row is None:
            return
        self.row.rank = self.rank_edit.text().strip()
        for name, edit in self.edits.items():
            setattr(self.row, name, edit.text().strip())
        self.row.set_other_text(self.other_edit.text().strip())
        self.changed.emit()

    def _on_section_changed(self, text: str) -> None:
        if self._loading or self.row is None:
            return
        for label, value in SECTION_CHOICES:
            if label == text:
                self.row.section = value
                break
        self.changed.emit()

    def _on_badge_changed(self, text: str) -> None:
        if self._loading or self.row is None:
            return
        badge = "" if text == BADGE_CHOICES[0] else text
        self.row.badge = badge
        self._apply_badge_look(badge)
        self.changed.emit()


__all__ = ["ChartCard", "FIELD_ROWS", "BADGE_CHOICES", "SECTION_CHOICES", "badge_color"]
