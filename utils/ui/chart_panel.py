"""「榜单」页：把识别出来的名次摆成一张可改的表，改完生成条目。

这是侧栏「刊」的第二页（用户 2026-11-15 要求），流程见 `utils/chart_editor.py`：

    ┌ 榜单：周刊UTAU排行榜 #52 · 43个名次 · 识别 38 帧            ┐
    ├ 发布时间 / 起始时间 / 终止时间 / 统计规则 / 榜单视频 / 上传者 / bilibili ┤
    ├ 视频简介（日文，可改）                                        ┤
    ├ [补全数据] [添加行] [删除选中] [恢复原样]                       ┤
    ├ 本周 │ 曲名 │ … │ 条目 │ 小节 │ 角标 │ 其他参数 │ 备注          ┤
    ├ 条目 wikitext 预览（跟着上面的改动实时变，只读）                 ┤
    └ 状态行 ………………………………………………… [取消] [生成条目]                ┘

要点：

* **每一格都能改**（双击）：AI 抄错的名次 / 曲名、没认出来的得点，都在这儿补；
  `其他参数` 一格写站上才有的参数（`翻唱 = 1; 评论权重 = 0.97`），值原样写进 bricks；
* **「小节」与「角标」是下拉框**：小节选「番外」的行会写进 `=== UTAU番外榜 ===` 那一节；
  角标选 PICKUP / TOPIC / 上周冠军 会自动补 `|color` 与 `|bottom-column`（颜色永远由名字查表）；
* **「补全数据」**：按曲名搜 niconico 补稿件编号 / 投稿时间 / 歌姬（改了曲名之后点它）；
* **「添加行」插在选中行下面**（没选中才追加到末尾），名次留空自己填 —— 视频漏了一个名次时
  这样才能补回原位；
* **备注列只读**：没有稿件编号、没取到投稿时间、同名待确认的行会标出来并把整行标黄，
  但不拦着你继续；
* 手填的译名 / 歌姬会记进 `user_names.yaml`，下次同一首歌自动沿用。
"""
import copy
import logging
from typing import Any, Dict, List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from utils import chart_editor, chart_entry, chart_recognize, chart_wikitext, user_dict
from utils.ui import theme, widgets
from utils.ui.workers import CallbackRelay, FunctionWorker

COLUMNS = ("本周", "曲名", "歌姬", "P主", "id", "时间", "得点", "再生", "评论", "收藏",
           "点赞", "条目", "翻唱", "小节", "角标", "其他参数", "备注")
COLUMN_WIDTHS = (64, 220, 130, 120, 84, 120, 74, 74, 60, 60, 60, 150, 56, 80, 96, 190, 150)
COLUMN_TIPS = (
    "名次：1、2、3…；「OP」「ED」这类非数字名次照抄",
    "曲名（保持视频里的日文原文；站上条目名不同就填在「条目」列）",
    "歌姬（多个用「、」分隔）",
    "P主 / P名（这里绝不能填歌姬名）",
    "niconico稿件编号（sm…）；空着就点「补全数据」按曲名搜",
    "投稿时间（JST，站上就是这么写的）",
    "得点（当周统计值，只能从视频里读）",
    "再生数（当周统计值）",
    "评论数（当周统计值）",
    "收藏 / マイリスト（当周统计值）",
    "点赞 / いいね（2023年后的新版视频才有）",
    "站内条目名（与曲名不同时才填，例如翻唱曲）",
    "翻唱：填1就写|翻唱 = 1（按n站标题/简介自动判断，可改）",
    "小节：主榜 / 番外（番外会单独写一节「=== UTAU番外榜 ===」）",
    "角标：PICKUP / TOPIC / 上周冠军（自动补|color与|bottom-column）",
    "其他参数：`翻唱 = 1; 评论权重 = 0.97`，用分号隔开，原样写进模板",
    "补全情况（只读）：没找到稿件 / 没有投稿时间的行会在这里说明",
)
ATTRS = ("rank", "title", "vocalist", "producer", "video_id", "time", "score", "view",
         "comment", "mylist", "like", "entry", "cover")
SECTION_COLUMN = len(ATTRS)
BADGE_COLUMN = SECTION_COLUMN + 1
OTHER_COLUMN = BADGE_COLUMN + 1
NOTE_COLUMN = OTHER_COLUMN + 1
EDITABLE = tuple(range(len(ATTRS))) + (OTHER_COLUMN,)
# 小节下拉：界面上写「主榜」，模型里存空串（主榜 / 番外之外不再支持）
SECTION_OPTIONS = ("主榜", "番外")
SECTION_LABELS = {chart_recognize.SECTION_MAIN: "主榜",
                  chart_recognize.SECTION_EXTRA: "番外"}
# 角标下拉：第一项表示「没有角标」
BADGE_OPTIONS = ("（无）",) + tuple(chart_wikitext.BADGE_NAMES)
NOTE_COLOR = QtGui.QColor("#fff6d5")
HEADER_FIELDS = (
    ("publish", "发布时间（UTC+8）", "视频投稿时间换算成UTC+8，站上就这么写"),
    ("start", "起始时间", "简介里「集計期間」的起点（JST原样）"),
    ("end", "终止时间", "简介里「集計期間」的终点（JST原样）"),
    ("rule", "统计规则", "2008规则写2008，2023规则写2023（影响导语与页脚导航框）"),
    ("video_id", "榜单视频", "这一期的niconico榜单视频编号（sm…）"),
    ("uploader", "上传者", "投稿者名字（写进导语）"),
    ("bilibili", "B站转载", "转载视频的 BV 号；留空就不写{{BilibiliVideo}}"),
)


class ChartPanel(QtWidgets.QWidget):
    """榜单页。保存发 `saved(work)`，取消发 `cancelled`。"""

    saved = QtCore.pyqtSignal(object)
    cancelled = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.work: Optional[chart_editor.ChartWork] = None
        self._original: Optional[chart_editor.ChartWork] = None
        self._loading = False
        self._rows: List[chart_editor.ChartRow] = []
        self._workers: List[FunctionWorker] = []
        self._relays: List[CallbackRelay] = []
        self._build_ui()

    # ------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 10)
        root.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel("榜单：—", self)
        font = self.title_label.font()
        font.setBold(True)
        self.title_label.setFont(font)
        top.addWidget(self.title_label, 1)
        self.summary_label = QtWidgets.QLabel("", self)
        self.summary_label.setStyleSheet(theme.quiet_label_style())
        top.addWidget(self.summary_label)
        root.addLayout(top)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.fields: Dict[str, QtWidgets.QLineEdit] = {}
        for index, (name, label, tip) in enumerate(HEADER_FIELDS):
            column = index % 4
            row = (index // 4) * 2
            caption = QtWidgets.QLabel(label, self)
            caption.setToolTip(tip)
            grid.addWidget(caption, row, column)
            edit = QtWidgets.QLineEdit(self)
            edit.setToolTip(tip)
            edit.editingFinished.connect(self._on_header_edited)
            self.fields[name] = edit
            grid.addWidget(edit, row + 1, column)
        root.addLayout(grid)

        desc_head = QtWidgets.QHBoxLayout()
        desc_head.addWidget(QtWidgets.QLabel("视频简介（写进「;视频简介」，可改）", self))
        self.desc_reset_button = QtWidgets.QPushButton("重新拆分", self)
        self.desc_reset_button.setToolTip("按站上的分行习惯把简介重新拆一遍（○… / PL: / バックナンバー）")
        self.desc_reset_button.clicked.connect(self._resplit_description)
        desc_head.addWidget(self.desc_reset_button)
        desc_head.addStretch(1)
        root.addLayout(desc_head)

        self.description = QtWidgets.QPlainTextEdit(self)
        self.description.setMinimumHeight(90)
        self.description.textChanged.connect(self._on_description_changed)
        root.addWidget(self.description)

        tools = QtWidgets.QHBoxLayout()
        self.enrich_button = QtWidgets.QPushButton("补全数据", self)
        self.enrich_button.setToolTip(
            "按曲名到niconico搜稿件，补上编号 / 投稿时间 / 歌姬（改了曲名之后点它）")
        self.enrich_button.clicked.connect(self._enrich)
        tools.addWidget(self.enrich_button)
        self.add_button = QtWidgets.QPushButton("添加行", self)
        self.add_button.setToolTip("加一行空名次（视频里漏掉的、或者要手动补的）")
        self.add_button.clicked.connect(self._add_row)
        tools.addWidget(self.add_button)
        self.remove_button = QtWidgets.QPushButton("删除选中", self)
        self.remove_button.setToolTip("这几行不写进条目")
        self.remove_button.clicked.connect(self._remove_rows)
        tools.addWidget(self.remove_button)
        self.restore_button = QtWidgets.QPushButton("恢复原样", self)
        self.restore_button.setToolTip("撤销表格里的改动，回到刚识别出来的样子")
        self.restore_button.clicked.connect(self._restore)
        tools.addWidget(self.restore_button)
        tools.addStretch(1)
        root.addLayout(tools)

        self.table = QtWidgets.QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        for index, width in enumerate(COLUMN_WIDTHS):
            self.table.setColumnWidth(index, width)
            self.table.horizontalHeaderItem(index).setToolTip(COLUMN_TIPS[index])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(240)
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, 3)

        notes_head = QtWidgets.QHBoxLayout()
        notes_head.addWidget(QtWidgets.QLabel("==杂谈==（留空就是「（待补）」）", self))
        notes_head.addStretch(1)
        root.addLayout(notes_head)
        self.notes = QtWidgets.QPlainTextEdit(self)
        self.notes.setMinimumHeight(50)
        self.notes.textChanged.connect(self._on_notes_changed)
        root.addWidget(self.notes)

        preview_head = QtWidgets.QHBoxLayout()
        preview_head.addWidget(QtWidgets.QLabel("条目wikitext预览（实时变，只读）", self))
        self.count_label = QtWidgets.QLabel("", self)
        self.count_label.setStyleSheet(theme.quiet_label_style())
        preview_head.addStretch(1)
        preview_head.addWidget(self.count_label)
        root.addLayout(preview_head)

        self.preview = QtWidgets.QPlainTextEdit(self)
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(150)
        self.preview.setStyleSheet("QPlainTextEdit { background: #ffffff; }")
        theme.scale_font(self.preview, theme.MONO_SIZE_PX, mono=True)
        root.addWidget(self.preview, 2)

        footer = QtWidgets.QHBoxLayout()
        hint = QtWidgets.QLabel(
            "手填的译名与歌姬会记进user_names.yaml，下次同一首歌自动沿用", self)
        hint.setStyleSheet(theme.quiet_label_style())
        root.addWidget(hint)
        self.status_label = QtWidgets.QLabel("就绪", self)
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        self.cancel_button = QtWidgets.QPushButton("取消", self)
        self.cancel_button.clicked.connect(self.cancelled.emit)
        footer.addWidget(self.cancel_button)
        self.save_button = QtWidgets.QPushButton("生成条目", self)
        self.save_button.setToolTip("按表格生成条目，然后转到「提交」页预览并提交")
        self.save_button.setDefault(True)
        theme.mark_accent(self.save_button)
        self.save_button.clicked.connect(self._on_save)
        footer.addWidget(self.save_button)
        root.addLayout(footer)

    # ------------------------------------------------------------ 开 / 关
    def start(self, payload: Dict[str, Any]) -> None:
        """打开这一页：`payload["work"]` 是 `ChartWork`。"""
        work = (payload or {}).get("work")
        self.work = work
        self._original = copy.deepcopy(work) if work is not None else None
        self._loading = True
        try:
            self.title_label.setText(f"榜单：{work.title}" if work else "榜单：—")
            self.summary_label.setText(work.summary() if work else "")
            for name, edit in self.fields.items():
                edit.setText(str(getattr(work, name, "") or "") if work else "")
            self.description.setPlainText(work.description if work else "")
            self.notes.setPlainText(work.notes if work else "")
            self._refresh_table()
        finally:
            self._loading = False
        self.enrich_button.setEnabled(bool(work and work.rows))
        self._update_preview()
        self.set_status(self._opening_hint(work))

    def _opening_hint(self, work) -> str:
        """打开时的提示：缺名次 / 没有 B站转载优先说（都是要动手补的）。"""
        notes: List[str] = []
        if work is not None:
            limit = chart_editor.main_limit()
            gaps = chart_recognize.missing_ranks(
                [{"本周": row.rank, "小节": row.section} for row in work.rows], limit=limit)
            for section, lacking in gaps.items():
                label = chart_recognize.NAME_OF_SECTION.get(section, section or "主榜")
                notes.append(f"{label}缺第{'、'.join(str(rank) for rank in lacking)}名"
                             "（选中上一行点「添加行」补）")
            if not str(work.bilibili or "").strip():
                notes.append("没搜到B站转载，可在「B站转载」栏直接填BV号")
        if not notes:
            return "每一格都能双击改；名次或曲名空着的行不会写进条目"
        return "；".join(notes)

    def reset(self) -> None:
        """丢掉上一轮的内容（「清除对话记录」时由主窗口调）。"""
        self.work = None
        self._original = None
        self._rows = []
        self._loading = True
        try:
            self.title_label.setText("榜单：—")
            self.summary_label.clear()
            self.table.setRowCount(0)
            self.description.clear()
            self.notes.clear()
            for edit in self.fields.values():
                edit.clear()
        finally:
            self._loading = False
        self.preview.clear()
        self.count_label.clear()
        self.set_status("等新一轮生成…")

    def set_status(self, text: str, kind: str = "") -> None:
        color = {"ok": "#14866d", "err": "#b32424", "warn": "#ac6600"}.get(kind, "#54595d")
        self.status_label.setStyleSheet(f"QLabel {{ color: {color}; }}")
        widgets.set_status_text(self.status_label, text, compact=(kind == "err"))

    # ------------------------------------------------------------ 表头 / 简介
    def _on_header_edited(self) -> None:
        if self._loading or self.work is None:
            return
        for name, edit in self.fields.items():
            setattr(self.work, name, edit.text().strip())
        self._update_preview()

    def _on_description_changed(self) -> None:
        if self._loading or self.work is None:
            return
        self.work.description = self.description.toPlainText()
        self._update_preview()

    def _on_notes_changed(self) -> None:
        if self._loading or self.work is None:
            return
        self.work.notes = self.notes.toPlainText()
        self._update_preview()

    def _resplit_description(self) -> None:
        """按站上的分行习惯把简介重新拆一遍（用户手改坏了可以退回来）。"""
        if self.work is None or self._original is None:
            return
        self._loading = True
        try:
            self.description.setPlainText(self._original.description)
        finally:
            self._loading = False
        self.work.description = self._original.description
        self._update_preview()
        self.set_status("简介已重新拆分", "ok")

    # ------------------------------------------------------------ 表格
    def _refresh_table(self) -> None:
        self._rows = list(self.work.rows) if self.work else []
        self.table.setRowCount(0)
        for row in self._rows:
            self._append_row(row)

    def _append_row(self, row: chart_editor.ChartRow) -> None:
        index = self.table.rowCount()
        self.table.insertRow(index)
        values = {column: str(getattr(row, name) or "") for column, name in enumerate(ATTRS)}
        values[OTHER_COLUMN] = row.other_text()
        values[NOTE_COLUMN] = row.note
        for column in range(self.table.columnCount()):
            item = QtWidgets.QTableWidgetItem(values.get(column, ""))
            if column not in EDITABLE:
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                item.setForeground(QtGui.QBrush(QtGui.QColor("#72777d")))
            if row.note:
                item.setToolTip(row.note)
                item.setBackground(QtGui.QBrush(NOTE_COLOR))
            self.table.setItem(index, column, item)
        self.table.setCellWidget(index, SECTION_COLUMN,
                                 self._section_box(index, row.section))
        self.table.setCellWidget(index, BADGE_COLUMN, self._badge_box(index, row.badge))

    def _section_box(self, index: int, section: str) -> QtWidgets.QComboBox:
        """小节下拉（主榜 / 番外）：两个选项就够了，避免手打串词。"""
        box = QtWidgets.QComboBox(self.table)
        box.addItems(list(SECTION_OPTIONS))
        box.setCurrentText(SECTION_LABELS.get(str(section or "").strip(),
                                              SECTION_OPTIONS[0]))
        box.currentTextChanged.connect(
            lambda text, position=index: self._on_section_changed(position, text))
        return box

    def _badge_box(self, index: int, badge: str) -> QtWidgets.QComboBox:
        """角标下拉（（无）/ PICKUP / TOPIC / 上周冠军）。"""
        box = QtWidgets.QComboBox(self.table)
        box.addItems(list(BADGE_OPTIONS))
        name = str(badge or "").strip()
        box.setCurrentText(name if name in BADGE_OPTIONS else BADGE_OPTIONS[0])
        box.currentTextChanged.connect(
            lambda text, position=index: self._on_badge_changed(position, text))
        return box

    def _on_section_changed(self, index: int, text: str) -> None:
        if self._loading or index >= len(self._rows):
            return
        section = chart_recognize.SECTION_EXTRA if text == SECTION_LABELS[
            chart_recognize.SECTION_EXTRA] else chart_recognize.SECTION_MAIN
        self._rows[index].section = section
        self._update_preview()

    def _on_badge_changed(self, index: int, text: str) -> None:
        if self._loading or index >= len(self._rows):
            return
        self._rows[index].badge = "" if text == BADGE_OPTIONS[0] else text
        self._update_preview()

    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        """格子改了就同步回模型（不重建整张表，免得光标乱跳）。"""
        if self._loading or self.work is None:
            return
        index, column = item.row(), item.column()
        if index >= len(self._rows) or column not in EDITABLE:
            return
        row = self._rows[index]
        text = item.text().strip()
        if column == OTHER_COLUMN:
            row.set_other_text(text)
        else:
            setattr(row, ATTRS[column], text)
            if column == ATTRS.index("title") and text:
                # 手填的曲名命中用户词典 → 自动补「条目」，省得每次重填译名；
                # 「条目」被用户手改过就不动它。
                remembered = user_dict.lookup_title(text)
                automatic = str(row.auto_entry or "").strip()
                if str(row.entry or "").strip() in ("", automatic):
                    row.entry = remembered
                    row.auto_entry = remembered
                    self._fill_cell(index, ATTRS.index("entry"), remembered)
        self._update_preview()

    def _fill_cell(self, index: int, column: int, text: str) -> None:
        item = self.table.item(index, column)
        if item is None:
            return
        was = self._loading
        self._loading = True
        try:
            item.setText(text)
        finally:
            self._loading = was

    def _add_row(self) -> None:
        """在**选中行下面**插一行（没选中就追加到末尾），名次留空由用户填。

        以前是追加到末尾并且自动写 `len+1`：想在中间补一个漏掉的名次时，
        新行跑到最后还顶着错的号（截图里没有第 7 名就是这么补不回来的）。
        """
        if self.work is None:
            return
        row = chart_editor.ChartRow()
        current = self.table.currentRow()
        position = len(self._rows) if current < 0 else min(current + 1, len(self._rows))
        rows = list(self.work.rows)
        rows.insert(position, row)
        self.work.rows = rows
        self._loading = True
        try:
            self._refresh_table()
        finally:
            self._loading = False
        self._update_preview()
        self.table.setCurrentCell(position, ATTRS.index("title"))
        item = self.table.item(position, ATTRS.index("title"))
        if item is not None:
            self.table.scrollToItem(item, QtWidgets.QAbstractItemView.PositionAtCenter)
            self.table.editItem(item)
        self.set_status(f"已在第{position}行插入空行，填上名次与曲名即可", "ok")

    def _remove_rows(self) -> None:
        if self.work is None:
            return
        indexes = sorted({item.row() for item in self.table.selectedIndexes()}, reverse=True)
        if not indexes:
            self.set_status("先在表里选中要删的行", "warn")
            return
        for index in indexes:
            if 0 <= index < len(self._rows):
                self.work.rows.remove(self._rows[index])
        self._loading = True
        try:
            self._refresh_table()
        finally:
            self._loading = False
        self._update_preview()
        self.set_status(f"已删掉{len(indexes)}行（点「恢复原样」可以撤销）", "ok")

    def _restore(self) -> None:
        """撤销表格改动，回到刚识别出来的样子。"""
        if self.work is None or self._original is None:
            return
        restored = copy.deepcopy(self._original)
        self.work.rows = restored.rows
        self._loading = True
        try:
            self._refresh_table()
        finally:
            self._loading = False
        self._update_preview()
        self.set_status("已恢复成刚识别出来的样子", "ok")

    # ------------------------------------------------------------ 补全数据
    def _enrich(self) -> None:
        """按曲名搜 niconico 补稿件编号 / 投稿时间 / 歌姬（网络请求，放线程里跑）。"""
        if self.work is None or not self.work.rows:
            self.set_status("表格里还没有名次", "warn")
            return
        self.enrich_button.setEnabled(False)
        self.set_status("正在按曲名补全稿件编号 / 投稿时间 / 歌姬…")
        worker = FunctionWorker(self._enrich_rows)
        relay = CallbackRelay(self._on_enriched, parent=self)
        worker.done.connect(relay.done)
        self._workers.append(worker)
        self._relays.append(relay)
        worker.finished.connect(lambda: self.enrich_button.setEnabled(True))
        worker.start()

    def _enrich_rows(self) -> Dict[str, Any]:
        """工作线程里跑：补全后把「补到几条」带回来。"""
        from utils.ui import RunCancelled

        rows = copy.deepcopy(self.work.rows) if self.work else []
        window = chart_editor.search_window(self.work) if self.work else (None, None)
        try:
            chart_editor.enrich_rows(rows, window=window)
            chart_entry.fill_entries(rows)
        except RunCancelled:
            return {"cancelled": True}          # 用户放弃了这一轮，不要再弹「补全失败」
        missing = sum(1 for row in rows if not row.video_id)
        return {"rows": rows, "missing": missing}

    def _on_enriched(self, result: Any) -> None:
        if isinstance(result, dict) and result.get("cancelled"):
            self.set_status("已放弃这一轮补全", "warn")
            return
        if self.work is None or not isinstance(result, dict):
            self.set_status("补全失败（详见日志）", "err")
            return
        self.work.rows = result.get("rows") or []
        self._loading = True
        try:
            self._refresh_table()
        finally:
            self._loading = False
        self._update_preview()
        missing = int(result.get("missing") or 0)
        if missing:
            self.set_status(f"补全完成，还有{missing}行没找到稿件（可以在表格里直接填sm号）",
                            "warn")
        else:
            self.set_status("补全完成，每行都有稿件编号了", "ok")

    # ------------------------------------------------------------ 预览 / 保存
    def _update_preview(self) -> None:
        if self.work is None:
            self.preview.clear()
            self.count_label.clear()
            return
        try:
            text = chart_wikitext.render(self.work)
        except Exception as error:                       # noqa: BLE001 - 预览不能把界面搞崩
            logging.error("渲染榜单条目失败：%s", error, exc_info=error)
            self.preview.setPlainText(f"（渲染失败：{error}）")
            return
        self.preview.setPlainText(text)
        rows = len([row for row in self.work.rows if not row.is_empty()])
        self.count_label.setText(f"{rows}个名次 · {len(text)}字")

    def _on_save(self) -> None:
        if self.work is None:
            return
        rows = [row for row in self.work.rows if not row.is_empty()]
        if not rows:
            self.set_status("一行名次都没有，条目里的榜单会是空的", "warn")
        missing = [row for row in rows if not row.video_id]
        if missing:
            self.set_status(f"{len(missing)}行没有稿件编号（照样能生成，之后可以在站上补）",
                            "warn")
        self.work.rows = list(self.work.rows)
        self.saved.emit(self.work)


__all__ = ["ChartPanel", "COLUMNS"]
