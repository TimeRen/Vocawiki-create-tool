"""「歌词」标签页：原 html/lyrics-editor.html 的 PyQt5 版。

四栏互相独立：左边待归类歌词，右边日语 / 中文 / 罗马音。
「自动识别并填入」「按行号转换」「AI 识别并填入」都调 utils/lyrics_editor.LyricsApi
（纯逻辑，和 html 版共用同一份），界面只负责收集参数、显示结果。

演唱者标记（{{LyricsKai/colors}}）：日语栏每一行都能点亮「谁唱」，
点某一行文字里的一句话可以在光标处切开、每段各选各的演唱者。
"""
import json
import logging
from typing import Any, Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from utils.string import is_empty


def _pane(title: str, parent=None) -> tuple:
    """一栏：标题（带行数）+ 文本框。"""
    holder = QtWidgets.QWidget(parent)
    layout = QtWidgets.QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    head = QtWidgets.QHBoxLayout()
    label = QtWidgets.QLabel(title, holder)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    count = QtWidgets.QLabel("0 行", holder)
    count.setStyleSheet("QLabel { color: #72777d; }")
    head.addWidget(label)
    head.addStretch(1)
    head.addWidget(count)
    layout.addLayout(head)
    edit = QtWidgets.QPlainTextEdit(holder)
    edit.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
    edit.setStyleSheet("QPlainTextEdit { font-family: Consolas, 'Cascadia Mono', monospace; "
                       "font-size: 12px; background: #ffffff; }")
    layout.addWidget(edit, 1)
    return holder, edit, count


class LyricsPanel(QtWidgets.QWidget):
    """歌词整理页（主窗口里的一页）。"""

    saved = QtCore.pyqtSignal(object)          # models.song.Lyrics
    cancelled = QtCore.pyqtSignal()

    # 悬停联动高亮的两档颜色（与原 html 版的 .hl-line / .hl-line.here 一致）
    HOVER_RGBA = (91, 108, 255)
    HOVER_ALPHA = 33                            # 别的栏
    HOVER_ALPHA_HERE = 66                       # 鼠标所在栏

    def __init__(self, parent=None):
        super().__init__(parent)
        self.api = None
        self._marks: Dict[str, Any] = {}
        self._splits: Dict[str, Dict[str, List[int]]] = {}
        self._marker_lines: List[str] = []
        self._charas: List[dict] = []
        self._busy = False
        self._panes: List[QtWidgets.QPlainTextEdit] = []
        self._hover_edit: Optional[QtWidgets.QPlainTextEdit] = None
        self._hover_line: Optional[int] = None
        self._marker_timer = QtCore.QTimer(self)
        self._marker_timer.setSingleShot(True)
        self._marker_timer.setInterval(400)
        self._marker_timer.timeout.connect(self._refresh_marker)
        self._build_ui()

    # ------------------------------------------------------------ 界面

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        actions = QtWidgets.QHBoxLayout()
        self.auto_button = QtWidgets.QPushButton("自动识别并填入", self)
        self.auto_button.clicked.connect(self._auto)
        actions.addWidget(self.auto_button)
        self.ai_button = QtWidgets.QPushButton("AI 识别并填入", self)
        self.ai_button.clicked.connect(self._ai_auto)
        actions.addWidget(self.ai_button)
        self.convert_button = QtWidgets.QPushButton("按行号转换", self)
        self.convert_button.clicked.connect(self._convert)
        actions.addWidget(self.convert_button)
        self.clear_button = QtWidgets.QPushButton("清空", self)
        self.clear_button.clicked.connect(self._clear)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        self.cancel_button = QtWidgets.QPushButton("取消", self)
        self.cancel_button.clicked.connect(self._on_cancel)
        actions.addWidget(self.cancel_button)
        self.done_button = QtWidgets.QPushButton("完成", self)
        self.done_button.setDefault(True)
        self.done_button.clicked.connect(self._on_done)
        actions.addWidget(self.done_button)
        root.addLayout(actions)

        params = QtWidgets.QHBoxLayout()
        params.addWidget(QtWidgets.QLabel("每组行数", self))
        self.group_spin = QtWidgets.QSpinBox(self)
        self.group_spin.setRange(1, 99)
        params.addWidget(self.group_spin)
        for attribute, label in (("jap_line_spin", "日语行号"), ("chs_line_spin", "中文行号"),
                                 ("roma_line_spin", "罗马音行号")):
            params.addWidget(QtWidgets.QLabel(label, self))
            spin = QtWidgets.QSpinBox(self)
            spin.setRange(1, 99)
            spin.setSpecialValueText("（无）")
            spin.setMinimum(0)
            spin.setValue(0)
            setattr(self, attribute, spin)
            params.addWidget(spin)
        hint = QtWidgets.QLabel("行号 = 每组里第几行（从 1 开始）；留空即不生成那一栏", self)
        hint.setStyleSheet("QLabel { color: #72777d; }")
        params.addWidget(hint)
        params.addStretch(1)
        root.addLayout(params)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        source_holder, self.source_edit, self.source_count = _pane("待归类歌词", splitter)
        jap_holder, self.jap_edit, self.jap_count = _pane("日语", splitter)
        chs_holder, self.chs_edit, self.chs_count = _pane("中文", splitter)
        roma_holder, self.roma_edit, self.roma_count = _pane("罗马音", splitter)
        for holder in (source_holder, jap_holder, chs_holder, roma_holder):
            splitter.addWidget(holder)
        splitter.setSizes([320, 260, 260, 200])
        root.addWidget(splitter, 1)
        for edit in (self.source_edit, self.jap_edit, self.chs_edit, self.roma_edit):
            edit.textChanged.connect(self._refresh_counts)
        self.jap_edit.textChanged.connect(self._schedule_marker)
        # 四栏悬停联动高亮：鼠标停在哪一行，四栏里的同一行一起亮
        self._panes = [self.source_edit, self.jap_edit, self.chs_edit, self.roma_edit]
        for edit in self._panes:
            edit.viewport().setMouseTracking(True)
            edit.viewport().installEventFilter(self)

        self.marker_box = QtWidgets.QGroupBox("歌唱者标记", self)
        marker_layout = QtWidgets.QVBoxLayout(self.marker_box)
        marker_layout.setContentsMargins(8, 6, 8, 6)
        marker_head = QtWidgets.QHBoxLayout()
        marker_hint = QtWidgets.QLabel(
            "每行点亮谁唱（可多点：多人＝组合渐变色，全点＝合唱）；"
            "点「切开」可在光标处把一行分成几段、每段各选各的", self.marker_box)
        marker_hint.setWordWrap(True)
        marker_hint.setStyleSheet("QLabel { color: #54595d; }")
        marker_head.addWidget(marker_hint, 1)
        clear_marks = QtWidgets.QPushButton("清空标记", self.marker_box)
        clear_marks.clicked.connect(self._clear_marks)
        marker_head.addWidget(clear_marks)
        marker_layout.addLayout(marker_head)
        self.legend = QtWidgets.QLabel("", self.marker_box)
        self.legend.setWordWrap(True)
        marker_layout.addWidget(self.legend)
        scroll = QtWidgets.QScrollArea(self.marker_box)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(120)
        self.marker_holder = QtWidgets.QWidget(scroll)
        self.marker_layout = QtWidgets.QVBoxLayout(self.marker_holder)
        self.marker_layout.setContentsMargins(2, 2, 2, 2)
        self.marker_layout.setSpacing(2)
        scroll.setWidget(self.marker_holder)
        marker_layout.addWidget(scroll, 1)
        self.marker_box.setVisible(False)
        root.addWidget(self.marker_box, 1)

        info = QtWidgets.QGridLayout()
        self.translator_edit = QtWidgets.QLineEdit(self)
        self.translator_url_edit = QtWidgets.QLineEdit(self)
        self.source_name_edit = QtWidgets.QLineEdit(self)
        self.source_url_edit = QtWidgets.QLineEdit(self)
        self.source_url_edit.setPlaceholderText("粘贴网易云 / bilibili 链接后可自动填翻译者")
        info.addWidget(QtWidgets.QLabel("翻译者", self), 0, 0)
        info.addWidget(self.translator_edit, 0, 1)
        info.addWidget(QtWidgets.QLabel("翻译链接", self), 0, 2)
        info.addWidget(self.translator_url_edit, 0, 3)
        info.addWidget(QtWidgets.QLabel("来源", self), 1, 0)
        info.addWidget(self.source_name_edit, 1, 1)
        info.addWidget(QtWidgets.QLabel("来源链接", self), 1, 2)
        url_row = QtWidgets.QHBoxLayout()
        url_row.addWidget(self.source_url_edit, 1)
        self.fill_button = QtWidgets.QPushButton("从链接填充", self)
        self.fill_button.clicked.connect(self._fill_source)
        url_row.addWidget(self.fill_button)
        info.addLayout(url_row, 1, 3)
        root.addLayout(info)

        footer = QtWidgets.QHBoxLayout()
        self.hover_check = QtWidgets.QCheckBox("使用 LyricsKai/hover（悬停显示译文）", self)
        self.hover_check.setToolTip("译文与原文排在同一行，鼠标悬停才显示译文；"
                                    "空行会自动补 #NoHover，并且不输出罗马音")
        footer.addWidget(self.hover_check)
        self.colors_check = QtWidgets.QCheckBox("使用 LyricsKai/colors（演唱者上色）", self)
        self.colors_check.setToolTip("按演唱者给歌词上色；需要在上面的「歌唱者标记」里给每行选演唱者")
        self.colors_check.toggled.connect(self._on_colors_toggled)
        footer.addWidget(self.colors_check)
        self.status_label = QtWidgets.QLabel("就绪", self)
        footer.addWidget(self.status_label)
        footer.addStretch(1)
        footer.addWidget(QtWidgets.QLabel("各栏都可以手动改 · 点「完成」把结果交回生成流程", self))
        root.addLayout(footer)

    # ------------------------------------------------------------ 启动

    def start(self, payload: Dict[str, Any]) -> None:
        api = (payload or {}).get("api")
        self.api = api
        if api is None:
            return
        context = api.get_context()
        self._charas = context.get("charas") or []
        self.source_edit.setPlainText(context.get("initial") or "")
        self.hover_check.setChecked(bool(context.get("useHover")))
        self.colors_check.setChecked(bool(context.get("useColors")))
        ai_context = context.get("aiLyrics") or {}
        self.ai_button.setVisible(not ai_context.get("hidden"))
        self.ai_button.setEnabled(bool(ai_context.get("enabled")))
        self.ai_button.setToolTip(ai_context.get("reason") or "把待归类歌词交给大模型分栏")
        self.hint_text = context.get("sourceHint") or "手动粘贴"
        self._marks = {}
        self._splits = {}
        self._refresh_counts()
        self._refresh_marker(force=True)
        self.set_status(f"来源：{self.hint_text}")
        self.source_edit.setFocus()

    def set_status(self, text: str, kind: str = "") -> None:
        color = {"ok": "#14866d", "err": "#b32424", "warn": "#ac6600"}.get(kind, "#54595d")
        self.status_label.setStyleSheet(f"QLabel {{ color: {color}; }}")
        self.status_label.setText(text)

    # ------------------------------------------------------------ 悬停联动高亮

    def eventFilter(self, watched, event) -> bool:                        # noqa: N802 - Qt 约定
        for pane in self._panes:
            if watched is not pane.viewport():
                continue
            if event.type() == QtCore.QEvent.MouseMove:
                self._set_hover_line(pane, self._line_at(pane, event.pos()))
            elif event.type() in (QtCore.QEvent.Leave, QtCore.QEvent.FocusOut):
                self._set_hover_line(None, None)
            break
        return super().eventFilter(watched, event)

    @staticmethod
    def _line_at(pane: QtWidgets.QPlainTextEdit, position: QtCore.QPoint) -> int:
        """鼠标位置对应的行下标（空文档返回 -1）。"""
        if not pane.toPlainText():
            return -1
        return pane.cursorForPosition(position).blockNumber()

    def _set_hover_line(self, pane: Optional[QtWidgets.QPlainTextEdit],
                        line: Optional[int]) -> None:
        if line is not None and line < 0:
            line = None
        if (pane, line) == (self._hover_edit, self._hover_line):
            return
        self._hover_edit, self._hover_line = pane, line
        self._apply_line_highlight()

    def _apply_line_highlight(self) -> None:
        """把同一行下标在四栏里都标出来（鼠标所在栏更深）。"""
        here = QtGui.QColor(*self.HOVER_RGBA, self.HOVER_ALPHA_HERE)
        other = QtGui.QColor(*self.HOVER_RGBA, self.HOVER_ALPHA)
        for pane in self._panes:
            selections = []
            if self._hover_line is not None:
                block = pane.document().findBlockByNumber(self._hover_line)
                if block.isValid():
                    selection = QtWidgets.QTextEdit.ExtraSelection()
                    cursor = QtGui.QTextCursor(block)
                    cursor.select(QtGui.QTextCursor.LineUnderCursor)
                    selection.cursor = cursor
                    fmt = QtGui.QTextCharFormat()
                    fmt.setBackground(here if pane is self._hover_edit else other)
                    fmt.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
                    selection.format = fmt
                    selections.append(selection)
            pane.setExtraSelections(selections)

    # ------------------------------------------------------------ 栏位

    def _count_lines(self, edit: QtWidgets.QPlainTextEdit) -> int:
        text = edit.toPlainText().strip()
        if not text:
            return 0
        return len(text.rstrip("\n").split("\n"))

    def _refresh_counts(self) -> None:
        for edit, label in ((self.source_edit, self.source_count), (self.jap_edit, self.jap_count),
                            (self.chs_edit, self.chs_count), (self.roma_edit, self.roma_count)):
            label.setText(f"{self._count_lines(edit)} 行")

    def _payload(self, extra: Optional[dict] = None) -> str:
        data = {
            "text": self.source_edit.toPlainText(),
            "jap": self.jap_edit.toPlainText(),
            "chs": self.chs_edit.toPlainText(),
            "roma": self.roma_edit.toPlainText(),
            "groupLength": str(self.group_spin.value() or ""),
            "japLine": str(self.jap_line_spin.value() or ""),
            "chsLine": str(self.chs_line_spin.value() or ""),
            "romaLine": str(self.roma_line_spin.value() or ""),
            "translator": self.translator_edit.text(),
            "translatorUrl": self.translator_url_edit.text(),
            "sourceName": self.source_name_edit.text(),
            "sourceUrl": self.source_url_edit.text(),
            "useHover": self.hover_check.isChecked(),
            "useColors": self.colors_check.isChecked(),
            "charaMarks": self._marks,
            "charaSplits": self._splits,
        }
        if extra:
            data.update(extra)
        return json.dumps(data, ensure_ascii=False)

    # ------------------------------------------------------------ 自动识别 / 转换

    def _auto(self) -> None:
        if self.api is None:
            return
        result = self.api.auto(self._payload({"japLine": "", "chsLine": "", "romaLine": ""}))
        if not result.get("ok"):
            self.set_status(str(result.get("error")), "err")
            return
        mode = result.get("mode")
        if mode == "lines":
            layout = result.get("layout") or {}
            self.group_spin.setValue(int(layout.get("group_length") or 0))
            for name, value in (("jap_line_spin", "jap_line"), ("chs_line_spin", "chs_line"),
                                ("roma_line_spin", "roma_line")):
                spin = getattr(self, name)
                spin.setValue(int(layout.get(value) or 0))
            self.set_status(str(result.get("message")), "warn")
            return
        self._fill_columns(result.get("jap"), result.get("chs"), result.get("roma"))
        self.set_status(str(result.get("message")), "ok")

    def _ai_auto(self) -> None:
        if self.api is None or self._busy:
            return
        self._busy = True
        self.ai_button.setEnabled(False)
        self.set_status("AI 正在分栏…")
        try:
            result = self.api.ai_auto(self._payload())
        finally:
            self._busy = False
            self.ai_button.setEnabled(True)
        if not result.get("ok"):
            self.set_status(str(result.get("error")), "err")
            return
        self._fill_columns(result.get("jap"), result.get("chs"), result.get("roma"))
        self.set_status("已由 AI 分栏（mode=ai）", "ok")

    def _convert(self) -> None:
        if self.api is None:
            return
        result = self.api.convert(self._payload())
        if not result.get("ok"):
            self.set_status(str(result.get("error")), "err")
            return
        self._fill_columns(result.get("jap"), result.get("chs"), result.get("roma"))
        self.set_status(str(result.get("message")), "ok")

    def _fill_columns(self, jap: Optional[str], chs: Optional[str], roma: Optional[str]) -> None:
        for edit, text in ((self.jap_edit, jap), (self.chs_edit, chs), (self.roma_edit, roma)):
            if text is None:
                continue
            edit.blockSignals(True)
            edit.setPlainText(text)
            edit.blockSignals(False)
        self._refresh_counts()
        self._refresh_marker(force=True)
        self._apply_line_highlight()

    def _clear(self) -> None:
        for edit in (self.source_edit, self.jap_edit, self.chs_edit, self.roma_edit):
            edit.blockSignals(True)
            edit.clear()
            edit.blockSignals(False)
        self._marks, self._splits = {}, {}
        self._refresh_counts()
        self._refresh_marker(force=True)
        self._set_hover_line(None, None)
        self.set_status("已清空")

    def _fill_source(self) -> None:
        if self.api is None:
            return
        url = self.source_url_edit.text().strip()
        if is_empty(url):
            self.set_status("请先填写来源链接", "warn")
            return
        self.set_status("正在读取链接…")
        result = self.api.fill_source(url)
        if not result.get("ok"):
            self.set_status(str(result.get("error")), "err")
            return
        self.translator_edit.setText(str(result.get("translator") or ""))
        self.translator_url_edit.setText(str(result.get("translatorUrl") or ""))
        self.source_name_edit.setText(str(result.get("sourceName") or ""))
        self.source_url_edit.setText(str(result.get("sourceUrl") or ""))
        self.set_status(str(result.get("message") or "已填充"), "ok")

    # ------------------------------------------------------------ 演唱者标记

    def _on_colors_toggled(self, checked: bool) -> None:
        self.marker_box.setVisible(checked)
        if checked:
            self._refresh_marker(force=True)

    def _schedule_marker(self) -> None:
        if self.colors_check.isChecked():
            self._marker_timer.start()

    def _refresh_marker(self, force: bool = False) -> None:
        if not self.colors_check.isChecked() and not force:
            return
        lines = self.jap_edit.toPlainText().split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
        if lines == self._marker_lines and not force:
            return
        self._marker_lines = lines
        self._build_legend()
        while self.marker_layout.count():
            item = self.marker_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for index, line in enumerate(lines):
            self.marker_layout.addWidget(self._mark_row(index, line))
        self.marker_layout.addStretch(1)

    def _build_legend(self) -> None:
        parts = [f"<span style='color:{item.get('color')}'>■</span> {item.get('name')}"
                 for item in self._charas]
        self.legend.setText("　".join(parts) or "（这首歌没有识别出歌姬，无法上色）")

    def _mark_row(self, index: int, line: str) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget(self.marker_holder)
        layout = QtWidgets.QVBoxLayout(row)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(2)
        head = QtWidgets.QHBoxLayout()
        number = QtWidgets.QLabel(f"{index + 1}", row)
        number.setMinimumWidth(28)
        number.setStyleSheet("QLabel { color: #72777d; }")
        head.addWidget(number)
        text_edit = QtWidgets.QLineEdit(line, row)
        text_edit.setStyleSheet("QLineEdit { font-family: Consolas, monospace; font-size: 12px; }")
        head.addWidget(text_edit, 1)
        split_button = QtWidgets.QToolButton(row)
        split_button.setText("切开")
        split_button.setToolTip("在光标处把这一行切开，切开的每段各选演唱者")
        split_button.clicked.connect(lambda: self._split_line(index, line, text_edit))
        head.addWidget(split_button)
        merge_button = QtWidgets.QToolButton(row)
        merge_button.setText("合并")
        merge_button.clicked.connect(lambda: self._merge_line(index))
        head.addWidget(merge_button)
        layout.addLayout(head)
        for segment_index, segment in enumerate(self._segments(index, line)):
            layout.addWidget(self._segment_row(index, line, segment_index, segment))
        return row

    def _segments(self, index: int, line: str) -> List[str]:
        """把一行按切分点切成若干段（没切过就是整行一段）。"""
        cuts = sorted({int(cut) for cut in (self._splits.get(str(index), {}).get("jap") or [])
                       if 0 < int(cut) < len(line)})
        if not cuts:
            return [line]
        parts = []
        start = 0
        for cut in cuts:
            parts.append(line[start:cut])
            start = cut
        parts.append(line[start:])
        return parts

    def _segment_row(self, index: int, line: str, segment_index: int,
                     segment: str) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget(self.marker_holder)
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(30, 0, 2, 0)
        layout.setSpacing(4)
        preview = QtWidgets.QLabel(segment or "（空）", row)
        preview.setStyleSheet("QLabel { color: #202122; }")
        preview.setMinimumWidth(120)
        layout.addWidget(preview, 1)
        marked = self._segment_marks(index, segment_index)
        for item in self._charas:
            name = item.get("name")
            button = QtWidgets.QToolButton(row)
            button.setText(name)
            button.setCheckable(True)
            button.setChecked(name in marked)
            button.setStyleSheet(
                f"QToolButton {{ border: 1px solid #d7dbe8; border-radius: 5px; padding: 1px 6px; }}"
                f"QToolButton:checked {{ background: {item.get('color')}; color: #ffffff; "
                f"border-color: transparent; }}")
            button.toggled.connect(
                lambda checked, key=index, seg=segment_index, who=name:
                self._toggle_mark(key, seg, who, checked))
            layout.addWidget(button)
        return row

    def _segment_marks(self, index: int, segment_index: int) -> List[str]:
        """这一段点亮的演唱者；未切分过时返回整行的标记（每段都算）。"""
        value = self._marks.get(str(index))
        if not value:
            return []
        if all(isinstance(item, str) for item in value):
            return list(value)
        if isinstance(value, list) and segment_index < len(value):
            return [name for name in value[segment_index] if name]
        return []

    def _toggle_mark(self, index: int, segment_index: int, name: str, checked: bool) -> None:
        key = str(index)
        segments = [list(self._segment_marks(index, i))
                    for i in range(len(self._segments(index, self._marker_lines[index])))]
        while len(segments) <= segment_index:
            segments.append([])
        current = segments[segment_index]
        if checked and name not in current:
            current.append(name)
        elif not checked and name in current:
            current.remove(name)
        if len(segments) == 1:
            self._marks[key] = [item for item in segments[0]]
        else:
            self._marks[key] = segments
        if not any(segments):
            self._marks.pop(key, None)

    def _split_line(self, index: int, line: str, text_edit: QtWidgets.QLineEdit) -> None:
        position = text_edit.cursorPosition()
        if position <= 0 or position >= len(line):
            self.set_status("请把光标放在要切开的位置（不能是行首行尾）", "warn")
            return
        key = str(index)
        tracks = self._splits.setdefault(key, {})
        cuts = sorted(set(tracks.get("jap") or []) | {position})
        tracks["jap"] = cuts
        self.set_status(f"第 {index + 1} 行已在第 {position} 个字前切开", "ok")
        self._refresh_marker(force=True)

    def _merge_line(self, index: int) -> None:
        key = str(index)
        self._splits.pop(key, None)
        value = self._marks.get(key)
        if isinstance(value, list) and value and isinstance(value[0], list):
            merged = [name for segment in value for name in segment]
            if merged:
                self._marks[key] = merged
            else:
                self._marks.pop(key, None)
        self.set_status(f"第 {index + 1} 行的切分已取消", "ok")
        self._refresh_marker(force=True)

    def _clear_marks(self) -> None:
        self._marks, self._splits = {}, {}
        self.set_status("已清空标记", "ok")
        self._refresh_marker(force=True)

    # ------------------------------------------------------------ 完成 / 取消

    def _on_done(self) -> None:
        if self.api is None:
            self.cancelled.emit()
            return
        result = self.api.save(self._payload())
        if not result.get("ok"):
            self.set_status(str(result.get("error")), "err")
            QtWidgets.QMessageBox.warning(self, "还不能完成", str(result.get("error")))
            return
        self.set_status("已保存歌词", "ok")
        self.saved.emit(self.api.result)

    def _on_cancel(self) -> None:
        if self.api is not None:
            self.api.cancel()
        self.cancelled.emit()
